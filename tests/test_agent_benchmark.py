"""Agent benchmark questions stay source-pinned and scoring is deterministic."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location(
    "agent_benchmark", _ROOT / "scripts" / "agent_benchmark.py"
)
benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(benchmark)


def test_questions_cover_every_bakeoff_document_and_pin_sources():
    questions = json.loads((_ROOT / "tests" / "agent_questions.json").read_text())["questions"]
    manifest = json.loads((_ROOT / "tests" / "bakeoff_manifest.json").read_text())["documents"]
    labels = {
        label["id"]: label
        for label in json.loads((_ROOT / "tests" / "bakeoff_labels.json").read_text())["documents"]
    }
    by_document = {question["document_id"]: question for question in questions}

    assert set(by_document) == {document["id"] for document in manifest}
    assert len({question["id"] for question in questions}) == len(questions)
    for document in manifest:
        question = by_document[document["id"]]
        assert question["source_sha256"] == document["sha256"]
        if document.get("page"):
            assert question["bundle_source_sha256"] == labels[document["id"]]["input_sha256"]
            assert question["pdf_pages"] == [document["page"]]
        else:
            assert question["bundle_source_sha256"] == document["sha256"]


def test_long_book_questions_pin_local_sources_and_cover_required_strata():
    question_file = json.loads(
        (_ROOT / "tests" / "long_book_agent_questions.json").read_text()
    )
    manifest = json.loads(
        (_ROOT / "tests" / "long_book_agent_manifest.json").read_text()
    )
    documents = {document["id"]: document for document in manifest["documents"]}
    questions = question_file["questions"]

    assert len({question["id"] for question in questions}) == len(questions)
    assert {question["document_id"] for question in questions} == set(documents)
    assert {question["representation"] for question in questions} == {
        "prose_fact",
        "equation_definition",
        "symbol_meaning",
        "table_cell",
        "figure",
        "cross_page_explanation",
        "reference",
        "unanswerable",
    }
    assert {question["document_class"] for question in questions} == {
        "technical_textbook"
    }
    assert sum(question["answer"]["kind"] == "refusal" for question in questions) == 1
    assert sum(
        question["expected_review_disposition"] == "action_required"
        for question in questions
    ) == 1
    for question in questions:
        document = documents[question["document_id"]]
        assert question["source_sha256"] == document["sha256"]
        assert question["bundle_source_sha256"] == document["sha256"]
        assert len(question["bundle_provenance_sha256"]) == 64
        assert question["bundle_pages"]
        assert question["pdf_pages"]
        assert question["evidence_block_ids"]
        assert question["ground_truth"].startswith("source PDF page")


def test_answer_scoring_pins_words_and_numeric_order():
    assert benchmark.score_answer("An ellipse", {"kind": "exact", "accepted": ["ellipse", "an ellipse"]})
    assert not benchmark.score_answer("elliptical", {"kind": "exact", "accepted": ["ellipse"]})
    assert benchmark.score_answer(
        "The geometric shape is an ellipse.",
        {"kind": "contains", "accepted": ["ellipse"]},
    )
    assert benchmark.score_answer(
        "The extension is .w.",
        {"kind": "contains", "accepted": ["w"]},
    )
    assert not benchmark.score_answer(
        "The extension is raw.",
        {"kind": "contains", "accepted": ["w"]},
    )
    assert benchmark.score_answer(
        "The gas has a higher molar entropy than the liquid.",
        {
            "kind": "contains_all",
            "accepted_groups": [["molar entropy"], ["gas", "vapour"], ["higher"]],
        },
    )
    assert not benchmark.score_answer(
        "The gas has a lower molar volume.",
        {
            "kind": "contains_all",
            "accepted_groups": [["molar entropy"], ["gas", "vapour"], ["higher"]],
        },
    )
    assert benchmark.score_answer("4, 9", {"kind": "numbers", "values": [4, 9], "tolerance": 0.05})
    assert not benchmark.score_answer("9 at x=4", {"kind": "numbers", "values": [4, 9], "tolerance": 0.05})
    assert benchmark.score_answer("1/2", {"kind": "number", "value": 0.5, "tolerance": 0})


def test_chunk_ranking_prefers_question_terms():
    chunks = [
        {"text": "unrelated prose", "section": {"title": "Introduction"}},
        {"text": "The binary file of radial wave functions uses extension w.",
         "section": {"title": "File naming"}},
    ]

    ranked = benchmark.rank_chunks("Which extension stores radial wave functions?", chunks, 1)

    assert ranked == [chunks[1]]


def test_chunk_ranking_prefers_distinct_exact_terms_over_repetition():
    repeated = {
        "text": "energy energetic energy energy represents representation",
        "section": {"title": "Energy"},
    }
    answer = {
        "text": "A line of constant energy in phase space is an ellipse.",
        "section": {"title": "Classical mechanics"},
    }

    ranked = benchmark.rank_chunks(
        "What shape represents a line of constant energy in phase space?",
        [repeated, answer],
        1,
    )

    assert ranked == [answer]


def test_bundle_context_keeps_text_chunks_after_asset_limit(tmp_path):
    version_dir = tmp_path / "document" / "v1"
    assets_dir = version_dir / "assets"
    assets_dir.mkdir(parents=True)
    (version_dir / "manifest.json").write_text("{}")
    (version_dir / "provenance.json").write_text('{"source_sha256": "abc"}')
    (assets_dir / "first.png").write_bytes(b"image")
    chunks = [
        {
            "id": "chunk-1",
            "markdown": "document.md#first",
            "pages": [1],
            "source_pages": ["source.pdf#page=1"],
            "text": "phase space background",
            "needs_review": True,
            "assets": ["assets/first.png"],
            "section": {"title": "Background"},
        },
        {
            "id": "chunk-2",
            "markdown": "document.md#answer",
            "pages": [2],
            "source_pages": ["source.pdf#page=2"],
            "text": "A line of constant energy is an ellipse.",
            "needs_review": False,
            "assets": [],
            "section": {"title": "Answer"},
        },
    ]
    (version_dir / "chunks.jsonl").write_text(
        "\n".join(json.dumps(chunk) for chunk in chunks) + "\n"
    )

    context, assets, review_flagged, selected_dir, calculation = benchmark._bundle_context(
        {
            "document_id": "document",
            "bundle_source_sha256": "abc",
            "question": "What shape is the line of constant energy in phase space?",
        },
        [tmp_path],
        max_chunks=2,
        max_assets=1,
    )

    assert "phase space background" in context
    assert "line of constant energy is an ellipse" in context
    assert assets == [assets_dir / "first.png"]
    assert review_flagged
    assert selected_dir == version_dir
    assert calculation is None


def test_passage_retrieval_rebuilds_from_provenance_and_keeps_page_precision(tmp_path):
    version_dir = tmp_path / "document" / "v1"
    version_dir.mkdir(parents=True)
    source_hash = "a" * 64
    blocks = [
        {
            "id": "#/answer",
            "type": "paragraph",
            "text": "A line of constant energy in phase space is an ellipse.",
            "page": 2,
            "bbox": {"x0": 10, "y0": 100, "x1": 200, "y1": 80},
            "confidence": 1.0,
            "engine": "docling",
            "coverage_status": "emitted",
            "extra": {},
        },
        {
            "id": "#/other",
            "type": "paragraph",
            "text": "Unrelated appendix material.",
            "page": 3,
            "bbox": {"x0": 10, "y0": 100, "x1": 200, "y1": 80},
            "confidence": 1.0,
            "engine": "mineru",
            "coverage_status": "emitted",
            "extra": {},
        },
    ]
    (version_dir / "provenance.json").write_text(json.dumps({
        "doc_id": source_hash,
        "source_path": "/books/source.pdf",
        "source_sha256": source_hash,
        "version": 1,
        "page_count": 3,
        "sections": {
            "id": "root",
            "title": "Mechanics",
            "depth": 0,
            "kind": "chapter",
            "page_start": 1,
            "block_ids": [block["id"] for block in blocks],
            "children": [],
        },
        "blocks": blocks,
        "tables": [],
        "figures": [],
        "coverage": {
            "doc_id": source_hash,
            "total_blocks": 2,
            "emitted": 2,
            "cropped": 0,
            "flagged": 0,
            "dropped": 0,
            "illegible": 0,
            "flags": [],
        },
    }))
    (version_dir / "manifest.json").write_text(json.dumps({
        "document": {"title": "Mechanics"},
        "read": {"markdown": ["document.md"]},
        "representations": {"page_images": []},
    }))
    (version_dir / "chunks.jsonl").write_text(json.dumps({
        "id": "chunk-answer",
        "markdown": "document.md",
        "pages": [2],
        "source_pages": ["../source.pdf#page=2"],
        "text": blocks[0]["text"],
        "assets": [],
        "needs_review": False,
        "section": {"title": "Mechanics"},
    }) + "\n")
    question = {
        "id": "shape",
        "document_id": "document",
        "bundle_source_sha256": source_hash,
        "bundle_pages": [2],
        "question": "What shape is a line of constant energy in phase space?",
    }

    passages = benchmark._retrieval_records(version_dir, "passages")
    report = benchmark.audit_retrieval([question], [tmp_path], 1)

    assert not (version_dir / "passages.jsonl").exists()
    assert len(passages) == 2
    assert passages[0]["pages"] == [2]
    assert passages[0]["search_text"].startswith("Document: Mechanics")
    chunk_summary = report["summary"]["chunks"]["1"]
    assert {key: value for key, value in chunk_summary.items() if key != "retrieval_duration_s"} == {
        "questions": 1,
        "answerable_questions": 1,
        "page_evaluable_questions": 1,
        "page_hits": 1,
        "mean_page_recall": 1.0,
        "mean_page_precision": 1.0,
        "review_queue": {
            "questions": 0,
            "true_positives": 0,
            "false_positives": 0,
            "false_negatives": 0,
            "precision": None,
            "recall": None,
        },
    }
    passage_summary = report["summary"]["passages"]["1"]
    assert {
        key: value for key, value in passage_summary.items()
        if key != "retrieval_duration_s"
    } == {
        key: value for key, value in chunk_summary.items()
        if key != "retrieval_duration_s"
    }
    assert report["comparison"]["budgets"]["1"] == {
        "page_hit_delta": 0,
        "mean_page_recall_delta": 0.0,
        "mean_page_precision_delta": 0.0,
        "passed": True,
    }
    assert report["comparison"]["passed"] is True


def test_bundle_selection_can_pin_conversion_provenance(tmp_path):
    versions = []
    for number, marker in ((1, "first"), (2, "second")):
        version = tmp_path / "document" / f"v{number}"
        version.mkdir(parents=True)
        (version / "manifest.json").write_text("{}")
        (version / "chunks.jsonl").write_text("")
        (version / "provenance.json").write_text(
            json.dumps({"source_sha256": "abc", "marker": marker})
        )
        versions.append(version)
    first_hash = hashlib.sha256(
        (versions[0] / "provenance.json").read_bytes()
    ).hexdigest()

    selected = benchmark._latest_bundle([tmp_path], "abc", first_hash)

    assert selected == versions[0]


def test_empty_answer_is_not_a_scientific_claim():
    expected = {"kind": "number", "value": 16, "tolerance": 0.05}

    assert benchmark.classify_answer("", expected, "length") == "refused"
    assert benchmark.classify_answer("insufficient evidence", expected, "stop") == "refused"
    assert benchmark.classify_answer("17", expected, "stop") == "incorrect"
    assert benchmark.classify_answer("16", expected, "stop") == "correct"


def test_expected_refusal_is_scored_as_correct():
    expected = {"kind": "refusal"}

    assert benchmark.classify_answer("insufficient evidence", expected, "stop") == "correct"
    assert benchmark.classify_answer("15.39072", expected, "stop") == "incorrect"


def test_numeric_questions_pin_queries_and_cover_ingestion_tasks():
    questions = json.loads(
        (_ROOT / "tests" / "numeric_agent_questions.json").read_text()
    )["questions"]

    assert [question["id"] for question in questions] == [
        "fischer-exact-cell",
        "fischer-continuation-join",
        "fischer-derived-ratio",
        "fischer-metadata-lookup",
        "fischer-unverified-refusal",
        "fischer-source-crop-provenance",
    ]
    assert all(question["required_evidence_fields"] for question in questions)
    assert all(question["required_citation_paths"] for question in questions)
    assert questions[4]["answer"] == {"kind": "refusal"}
    assert questions[5]["required_asset_paths"] == [
        "assets/mineru_15_table_82_p15.png"
    ]

    contexts = [
        benchmark._bundle_context(question, [_ROOT / "out"], 3, 3)
        for question in questions
    ]
    assert all(version.name == "v6" for _, _, _, version, _ in contexts)
    assert "#/mineru/15/table/82" in contexts[1][0]
    assert "#/mineru/15/table/81" in contexts[1][0]
    assert contexts[4][2]
    assert contexts[5][1] == [
        _ROOT
        / "out/0685e8d85e2237d8/v6/assets/mineru_15_table_82_p15.png"
    ]


def test_calculator_uses_the_two_pinned_best_values():
    question = json.loads(
        (_ROOT / "tests" / "numeric_calculator_questions.json").read_text()
    )["questions"][0]

    context, assets, review_flagged, version, calculation = benchmark._bundle_context(
        question, [_ROOT / "out"], 1, 1, calculator=True
    )

    assert version.name == "v6"
    assert assets == []
    assert not review_flagged
    assert calculation == {
        "operation": "divide",
        "operands": ["4.753337", "1.835914"],
        "round_decimal_places": 6,
        "calculator_result": "2.589085",
        "operand_selection_valid": True,
        "arithmetic_matches_label": True,
    }
    assert '"calculator_result": "2.589085"' in context


def test_equation_figure_questions_pin_sources_and_required_crops():
    questions = json.loads(
        (_ROOT / "tests" / "equation_figure_agent_questions.json").read_text()
    )["questions"]
    manifest = json.loads(
        (_ROOT / "tests" / "equation_figure_agent_manifest.json").read_text()
    )["documents"]
    source_hashes = {document["id"]: document["sha256"] for document in manifest}

    assert [question["category"] for question in questions] == [
        "equation", "equation", "equation", "figure", "figure", "figure"
    ]
    assert len({question["id"] for question in questions}) == 6
    for question in questions:
        assert question["source_sha256"] == source_hashes[question["document_id"]]
        assert question["bundle_source_sha256"] == question["source_sha256"]
        assert len(question["bundle_provenance_sha256"]) == 64
        assert question["bundle_page_context"] is True
        assert len(question["required_asset_paths"]) == 1


def test_equation_figure_context_opens_each_pinned_asset():
    questions = json.loads(
        (_ROOT / "tests" / "equation_figure_agent_questions.json").read_text()
    )["questions"]

    for question in questions:
        _, assets, review_flagged, version_dir, calculation = benchmark._bundle_context(
            question, [_ROOT / "out"], 1, 1
        )
        opened = {path.relative_to(version_dir).as_posix() for path in assets}

        assert set(question["required_asset_paths"]) <= opened
        assert len(opened) == 1
        assert review_flagged
        assert calculation is None


def test_required_bundle_crop_is_scored_only_in_bundle_mode(tmp_path):
    crop = tmp_path / "assets" / "equation.png"
    crop.parent.mkdir()
    crop.write_bytes(b"image")

    assert benchmark._required_assets_opened(
        "bundle", tmp_path, ["assets/equation.png"], [crop]
    )
    assert not benchmark._required_assets_opened(
        "bundle", tmp_path, ["assets/equation.png"], []
    )
    assert benchmark._required_assets_opened(
        "pdf", tmp_path, ["assets/equation.png"], []
    )


def test_responses_api_keeps_image_bytes_out_of_text_prompt(tmp_path):
    crop = tmp_path / "equation.png"
    crop.write_bytes(b"image")

    class Responses:
        request = None

        @classmethod
        def create(cls, **kwargs):
            cls.request = kwargs
            return SimpleNamespace(
                output_text='{"answer":"sqrt(d_k)","citations":[]}',
                status="completed",
                usage=SimpleNamespace(input_tokens=101, output_tokens=12),
            )

    raw, usage = benchmark._ask(
        SimpleNamespace(responses=Responses),
        "responses",
        "model",
        "What is the denominator?",
        "page 4",
        [crop],
        64,
        "none",
        0,
    )

    content = Responses.request["input"][0]["content"]
    assert [part["type"] for part in content] == ["input_text", "input_image"]
    assert "data:image/png;base64" not in content[0]["text"]
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert raw.startswith('{"answer"')
    assert usage == {
        "finish_reason": "stop",
        "input_tokens": 101,
        "output_tokens": 12,
    }


def test_responses_text_accepts_mlx_vlm_message_without_type_fields():
    response = SimpleNamespace(
        output_text="",
        output=[SimpleNamespace(content=[SimpleNamespace(text="sqrt(d_k)")])],
    )

    assert benchmark._responses_text(response) == "sqrt(d_k)"


def test_numeric_reply_requires_reported_fields_and_artifact_citation():
    raw = json.dumps({
        "answer": "1.835914",
        "citations": [{"path": "data/tables/page_006_panels.csv", "page": 6}],
        "evidence_fields_used": ["best_value", "confidence", "verification_status"],
    })

    answer, citations, fields = benchmark._parse_reply(raw)

    assert answer == "1.835914"
    assert benchmark._citation_valid(
        citations, [6], ["data/tables/page_006_panels.csv"]
    )
    assert benchmark._required_fields_used(
        fields, ["best_value", "confidence", "verification_status"]
    )
    assert not benchmark._required_fields_used(fields, ["reader_refusal_reason"])


def test_numeric_benchmark_snapshot_pins_questions_and_measured_failure():
    question_path = _ROOT / "tests" / "numeric_agent_questions.json"
    snapshot = json.loads(
        (_ROOT / "docs" / "agent-benchmark-numeric-2026-08-15.json").read_text()
    )

    assert snapshot["questions_sha256"] == hashlib.sha256(
        question_path.read_bytes()
    ).hexdigest()
    assert snapshot["summary"] == {
        "runs": 6,
        "answer_correct": 5,
        "passed_all_task_gates": 5,
        "correct_refusals": 1,
        "valid_artifact_citations": 6,
        "valid_required_evidence_fields": 6,
        "required_assets_opened": 1,
        "release_blockers": 1,
        "input_tokens": 4559,
        "output_tokens": 518,
    }
    failed = [result for result in snapshot["results"] if not result["passed"]]
    assert failed == [{
        "question_id": "fischer-derived-ratio",
        "answer": "2.589076",
        "expected": "2.589085",
        "answer_correct": False,
        "passed": False,
        "citation": "data/tables/page_006_panels.csv",
        "evidence_fields_used": ["best_value"],
        "assets_opened": [],
        "input_tokens": 402,
    }]


def test_equation_figure_snapshot_pins_measured_failure_and_context_gain():
    question_path = _ROOT / "tests" / "equation_figure_agent_questions.json"
    snapshot = json.loads(
        (_ROOT / "docs" / "agent-benchmark-equation-figure-2026-08-15.json")
        .read_text()
    )

    assert snapshot["questions_sha256"] == hashlib.sha256(
        question_path.read_bytes()
    ).hexdigest()
    assert snapshot["summary"] == {
        "bundle": {
            "correct": 5,
            "runs": 6,
            "valid_page_citations": 6,
            "required_assets_opened": 6,
            "input_tokens": 13215,
            "release_blockers": 1,
        },
        "pdf": {
            "correct": 5,
            "runs": 6,
            "valid_page_citations": 6,
            "required_assets_opened": 6,
            "input_tokens": 17206,
            "release_blockers": 0,
        },
        "bundle_input_token_reduction": 0.232,
        "bundle_accuracy_minus_pdf": 0.0,
        "paired_exit_criteria_passed": True,
        "all_task_gates_passed": False,
    }
    failures = [
        result for result in snapshot["results"] if result["outcome"] != "correct"
    ]
    assert [(result["mode"], result["answer"], result["expected"]) for result in failures] == [
        ("bundle", "rho_0", "nu"),
        ("pdf", "rho", "nu"),
    ]


def test_calculator_snapshot_pins_control_and_assisted_outcomes():
    question_path = _ROOT / "tests" / "numeric_calculator_questions.json"
    snapshot = json.loads(
        (_ROOT / "docs" / "agent-benchmark-calculator-2026-08-15.json").read_text()
    )

    assert snapshot["questions_sha256"] == hashlib.sha256(
        question_path.read_bytes()
    ).hexdigest()
    assert snapshot["control"]["answer"] == "2.587225"
    assert snapshot["control"]["answer_correct"] is False
    assert snapshot["control"]["release_blockers"] == 1
    assert snapshot["assisted"]["operands"] == ["4.753337", "1.835914"]
    assert snapshot["assisted"]["calculator_result"] == "2.589085"
    assert snapshot["assisted"]["operand_selection_valid"] is True
    assert snapshot["assisted"]["arithmetic_valid"] is True
    assert snapshot["assisted"]["answer_correct"] is True
    assert snapshot["assisted"]["release_blockers"] == 0


def test_citation_pages_accept_json_strings_without_accepting_other_pages():
    assert benchmark._citation_has_page([{"path": "document.md", "page": "37"}], [37])
    assert not benchmark._citation_has_page([{"path": "document.md", "page": "38"}], [37])


def test_summary_compares_accuracy_and_context_by_mode():
    results = [
        {
            "mode": "bundle", "correct": True, "outcome": "correct",
            "citation_page_valid": True, "assets_opened": [],
            "input_tokens": 25, "output_tokens": 5, "release_blocker": False,
        },
        {
            "mode": "bundle", "correct": False, "outcome": "refused",
            "citation_page_valid": False, "assets_opened": ["crop.png"],
            "input_tokens": 25, "output_tokens": 10, "release_blocker": False,
        },
        {
            "mode": "pdf", "correct": True, "outcome": "correct",
            "citation_page_valid": True, "assets_opened": ["page.png"],
            "input_tokens": 100, "output_tokens": 8, "release_blocker": False,
        },
        {
            "mode": "pdf", "correct": True, "outcome": "correct",
            "citation_page_valid": True, "assets_opened": ["page.png"],
            "input_tokens": 100, "output_tokens": 8, "release_blocker": False,
        },
    ]

    summary = benchmark.summarize_results(results)

    assert summary["by_mode"]["bundle"] == {
        "runs": 2,
        "correct": 1,
        "passed": 1,
        "correct_refusals": 0,
        "incorrect": 0,
        "refused": 1,
        "valid_citations": 1,
        "evidence_fields_scored": 0,
        "valid_evidence_fields": 0,
        "required_assets_scored": 0,
        "required_assets_opened": 0,
        "release_blockers": 0,
        "errors": 0,
        "assets_opened": 1,
        "input_tokens": 50,
        "output_tokens": 15,
        "calculator_scored": 0,
        "calculator_operand_selection_valid": 0,
        "calculator_arithmetic_valid": 0,
        "evidence_duration_s": 0,
        "inference_duration_s": 0,
    }
    assert summary["comparison"] == {
        "bundle_input_token_reduction": 0.75,
        "bundle_accuracy_minus_pdf": -0.5,
    }
    assert summary["by_representation"]["unspecified"]["runs"] == 4
    assert summary["by_document_class"]["unspecified"]["runs"] == 4
    assert summary["review_queue"]["questions"] == 0
    assert benchmark.evaluate_exit_criteria(summary) == {
        "minimum_bundle_input_token_reduction": 0.2,
        "accuracy_pass": False,
        "context_pass": True,
        "passed": False,
    }


def test_exit_criteria_require_material_context_reduction():
    summary = {
        "by_mode": {
            "bundle": {"correct": 2},
            "pdf": {"correct": 2},
        },
        "comparison": {"bundle_input_token_reduction": 0.19},
    }

    assert benchmark.evaluate_exit_criteria(summary) == {
        "minimum_bundle_input_token_reduction": 0.2,
        "accuracy_pass": True,
        "context_pass": False,
        "passed": False,
    }


def test_review_queue_metrics_report_precision_and_recall():
    results = [
        {
            "review_queue_scored": True,
            "expected_action_required": True,
            "review_flagged": True,
        },
        {
            "review_queue_scored": True,
            "expected_action_required": False,
            "review_flagged": True,
        },
        {
            "review_queue_scored": True,
            "expected_action_required": True,
            "review_flagged": False,
        },
    ]

    assert benchmark._review_queue_metrics(results) == {
        "questions": 3,
        "true_positives": 1,
        "false_positives": 1,
        "false_negatives": 1,
        "precision": 0.5,
        "recall": 0.5,
    }


def test_review_queue_uses_labelled_evidence_blocks(tmp_path):
    (tmp_path / "review.json").write_text(json.dumps({
        "items": [
            {"block_id": "#/flagged", "disposition": "action_required"},
            {"block_id": "#/source", "disposition": "source_dependent"},
        ]
    }))

    assert benchmark._queue_flags_evidence(
        {"evidence_block_ids": ["#/flagged"]}, tmp_path
    )
    assert not benchmark._queue_flags_evidence(
        {"evidence_block_ids": ["#/source"]}, tmp_path
    )

"""Optional DOI registry enrichment through CSL-JSON content negotiation.

The raw response remains in the bundle. Merging records evidence and conflicts
without making registry access part of the offline conversion default.
"""

from __future__ import annotations

import copy
import html
import json
import re
import urllib.parse
import urllib.request

from pdf2md.logging import get_logger


log = get_logger("doi_metadata")
DOI_METADATA_NAME = "data/doi-metadata.csl.json"


def fetch_doi_metadata(
    doi: str,
    *,
    timeout: float = 20.0,
    opener=urllib.request.urlopen,
) -> dict | None:
    """Retrieve one DOI record as CSL-JSON; failure leaves local metadata intact."""
    url = f"https://doi.org/{urllib.parse.quote(doi, safe='/')}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.citationstyles.csl+json",
            "User-Agent": "pdf2md metadata enrichment",
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            record = json.loads(response.read())
            return record if isinstance(record, dict) else None
    except Exception as exc:  # noqa: BLE001 - optional evidence must degrade cleanly
        log.warning("DOI metadata lookup failed for %s (%s); keeping local metadata", doi, exc)
        return None


def _csl_names(record: dict) -> list[str] | None:
    names = []
    for person in record.get("author") or []:
        if person.get("literal"):
            names.append(" ".join(person["literal"].split()))
            continue
        name = " ".join(
            part.strip()
            for part in (person.get("given", ""), person.get("family", ""))
            if part and part.strip()
        )
        if name:
            names.append(name)
    return names or None


def _csl_date(record: dict) -> str | None:
    parts = ((record.get("issued") or {}).get("date-parts") or [])
    if not parts or not parts[0]:
        return None
    values = [int(value) for value in parts[0][:3]]
    return "-".join(
        [f"{values[0]:04d}", *(f"{value:02d}" for value in values[1:])]
    )


def _csl_value(record: dict, key: str):
    value = record.get(key)
    if isinstance(value, list):
        cleaned = [item for item in value if item]
        return cleaned or None
    return value or None


def _csl_text(record: dict, key: str) -> str | None:
    value = _csl_value(record, key)
    if not isinstance(value, str):
        return None
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split()) or None


def _same_field(left, right) -> bool:
    normalize = lambda value: re.sub(r"\W+", " ", str(value).casefold()).strip()
    if isinstance(left, list) and isinstance(right, list):
        return [normalize(item) for item in left] == [normalize(item) for item in right]
    return normalize(left) == normalize(right)


def merge_doi_metadata(meta: dict, record: dict) -> dict:
    """Merge an exact-DOI CSL record while retaining local selections and conflicts."""
    issued = _csl_date(record)
    registry = {
        "title": _csl_text(record, "title"),
        "authors": _csl_names(record),
        "year": issued[:4] if issued else None,
        "doi": _csl_value(record, "DOI") or _csl_value(record, "doi"),
        "venue": _csl_text(record, "container-title"),
        "publisher": _csl_text(record, "publisher"),
        "volume": _csl_value(record, "volume"),
        "issue": _csl_value(record, "issue"),
        "pages": _csl_value(record, "page"),
        "article_number": _csl_value(record, "article-number"),
        "issn": _csl_value(record, "ISSN"),
        "isbn": _csl_value(record, "ISBN"),
        "abstract": _csl_text(record, "abstract"),
        "license": _csl_value(record, "license"),
    }
    merged = copy.deepcopy(meta)
    evidence = merged.setdefault("metadata_evidence", {})
    registry_preferred = {
        "venue", "publisher", "volume", "issue", "pages", "article_number", "issn",
    }
    for field, value in registry.items():
        if value is None:
            continue
        field_evidence = evidence.setdefault(
            field,
            {"selected": None, "alternatives": [], "rejected": []},
        )
        record_evidence = {
            "value": value,
            "quality": "high",
            "evidence": [{
                "source": "doi_registry",
                "doi": meta.get("doi") or registry.get("doi"),
                "artifact": DOI_METADATA_NAME,
            }],
            "penalties": [],
        }
        current = merged.get(field)
        if current is None:
            merged[field] = value
            field_evidence["selected"] = record_evidence
        elif _same_field(current, value):
            selected = field_evidence.get("selected")
            if selected:
                selected.setdefault("evidence", []).extend(record_evidence["evidence"])
            else:
                field_evidence["selected"] = record_evidence
        elif field in registry_preferred:
            previous = copy.deepcopy(field_evidence.get("selected")) or {
                "value": current,
                "quality": "medium",
                "evidence": [{"source": "local_extraction"}],
                "penalties": [],
            }
            previous["not_selected_reason"] = "registry_structured_field_preferred"
            field_evidence.setdefault("alternatives", []).append(previous)
            merged[field] = value
            field_evidence["selected"] = record_evidence
        else:
            record_evidence["not_selected_reason"] = "local_selection_preserved"
            field_evidence.setdefault("alternatives", []).append(record_evidence)

    if issued:
        dates = dict(merged.get("publication_dates") or {})
        dates.setdefault("issued", issued)
        merged["publication_dates"] = dates
        date_evidence = evidence.setdefault(
            "publication_dates",
            {"selected": None, "alternatives": [], "rejected": []},
        )
        if date_evidence.get("selected"):
            date_evidence["selected"]["value"] = dates
            date_evidence["selected"].setdefault("evidence", []).append({
                "source": "doi_registry",
                "doi": meta.get("doi") or registry.get("doi"),
                "artifact": DOI_METADATA_NAME,
            })
        else:
            date_evidence["selected"] = {
                "value": dates,
                "quality": "high",
                "evidence": [{
                    "source": "doi_registry",
                    "doi": meta.get("doi") or registry.get("doi"),
                    "artifact": DOI_METADATA_NAME,
                }],
                "penalties": [],
            }
    merged["metadata_source"] = "doi_registry"
    merged["doi_registry_path"] = DOI_METADATA_NAME
    merged["registry_type"] = record.get("type")
    return merged

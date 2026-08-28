#!/usr/bin/env bash
# Repeat one MinerU parse under an immutable image and offline model snapshot.
# Each run gets a new output directory; existing results are never overwritten.

set -euo pipefail

image_id="sha256:b1ddeb898393a2cdfeebced56e62ba7bc77364d235eb577fd955bf62d8b9e33d"
source_pdf="${1:?usage: run_pinned_mineru_repeats.sh SOURCE_PDF OUTPUT_ROOT [REPEATS]}"
output_root="${2:?usage: run_pinned_mineru_repeats.sh SOURCE_PDF OUTPUT_ROOT [REPEATS]}"
repeats="${3:-3}"
expected_source="0685e8d85e2237d8795a1fb48df01fba50a7a79cf207787cce31955487d0eb47"

actual_source="$(sha256sum "$source_pdf" | cut -d' ' -f1)"
if [[ "$actual_source" != "$expected_source" ]]; then
    echo "source hash mismatch: $actual_source" >&2
    exit 1
fi

actual_image="$(docker image inspect "$image_id" --format '{{.Id}}')"
if [[ "$actual_image" != "$image_id" ]]; then
    echo "image ID mismatch: $actual_image" >&2
    exit 1
fi

mkdir -p "$output_root"
for run_number in $(seq 1 "$repeats"); do
    run_id="$(printf '%02d' "$run_number")"
    run_dir="$output_root/run-$run_id"
    if [[ -e "$run_dir" ]]; then
        echo "refusing to overwrite $run_dir" >&2
        exit 1
    fi
    mkdir "$run_dir"
    started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "[$run_id/$repeats] starting at $started_at"
    docker run --rm \
        --gpus all \
        --network none \
        --ipc host \
        -e PYTHONHASHSEED=0 \
        -e HF_HUB_OFFLINE=1 \
        -e TRANSFORMERS_OFFLINE=1 \
        -e CUBLAS_WORKSPACE_CONFIG=:4096:8 \
        -v "$source_pdf:/work/input/fischer.pdf:ro" \
        -v "$run_dir:/work/output" \
        "$image_id" \
        mineru \
            -p /work/input/fischer.pdf \
            -o /work/output \
            -b hybrid-engine \
            --effort high \
            --image-analysis true \
        2>&1 | tee "$run_dir/run.log"
    finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    middle_json="$(find "$run_dir" -type f -name '*_middle.json' -print -quit)"
    if [[ -z "$middle_json" ]]; then
        echo "run $run_id produced no middle JSON" >&2
        exit 1
    fi
    sha256sum "$middle_json" > "$run_dir/middle.sha256"
    {
        echo "started_at=$started_at"
        echo "finished_at=$finished_at"
        echo "source_sha256=$actual_source"
        echo "image_id=$actual_image"
        echo "pythonhashseed=0"
        echo "cublas_workspace_config=:4096:8"
        echo "network=none"
    } > "$run_dir/settings.txt"
    echo "[$run_id/$repeats] finished at $finished_at"
done

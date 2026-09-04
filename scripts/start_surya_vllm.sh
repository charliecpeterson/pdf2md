#!/usr/bin/env bash
# Start the inference server Marker's Surya needs, without the nvidia docker runtime.
#
# Surya spawns `vllm/vllm-openai` with `--runtime nvidia`, which many hosts never
# register even with the container toolkit installed. `--gpus all` works on those
# hosts, so this starts the same server by hand; point Marker at it with
#   export SURYA_INFERENCE_URL=http://127.0.0.1:8000/v1
#
# --deterministic drops speculative decoding and pins a seed. It does NOT make
# Marker reproducible: vLLM's continuous batching leaves the numerics
# batch-dependent, measured at roughly 0.1% of blocks on a 545-page book. Kept
# because it is the configuration that question was settled with.
set -u
PORT="${PORT:-8000}"
EXTRA=()
[ "${1:-}" = "--deterministic" ] && EXTRA=(--seed 0)
[ "${1:-}" != "--deterministic" ] && EXTRA=(--speculative-config '{"method": "mtp", "num_speculative_tokens": 2}')

docker rm -f "surya-vllm-$PORT" >/dev/null 2>&1
docker run --rm -d --name "surya-vllm-$PORT" \
    --gpus all \
    -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
    -p "$PORT:8000" --ipc=host \
    vllm/vllm-openai:v0.20.1 \
    --model datalab-to/surya-ocr-2 \
    --no-enforce-eager \
    --max-num-seqs 32 \
    --dtype bfloat16 \
    --max-model-len 18000 \
    --max-num-batched-tokens 8192 \
    --gpu-memory-utilization 0.85 \
    --enable-prefix-caching \
    --mm-processor-kwargs '{"min_pixels": 3136, "max_pixels": 6291456}' \
    --served-model-name datalab-to/surya-ocr-2 \
    "${EXTRA[@]}"

echo "starting; wait for it with:"
echo "  until curl -s http://127.0.0.1:$PORT/v1/models | grep -q surya; do sleep 20; done"

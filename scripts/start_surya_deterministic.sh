#!/usr/bin/env bash
# The determinism probe: same server, minus MTP speculative decoding, with a seed.
# Surya already decodes at temperature 0, so any remaining variation comes from
# vLLM's batching numerics rather than sampling.
set -u
docker rm -f surya-vllm-8000 >/dev/null 2>&1
docker run --rm -d --name surya-vllm-8000 \
    --gpus all \
    -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
    -p 8000:8000 --ipc=host \
    vllm/vllm-openai:v0.20.1 \
    --model datalab-to/surya-ocr-2 \
    --no-enforce-eager \
    --max-num-seqs 32 \
    --dtype bfloat16 \
    --max-model-len 18000 \
    --max-num-batched-tokens 8192 \
    --gpu-memory-utilization 0.85 \
    --enable-prefix-caching \
    --seed 0 \
    --mm-processor-kwargs '{"min_pixels": 3136, "max_pixels": 6291456}' \
    --served-model-name datalab-to/surya-ocr-2

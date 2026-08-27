# Qwen3 embedding runtime-matrix benchmark image.
#
# Runs `corpus-steward model-artifact-manifest` (pin) then
# `corpus-steward qwen-runtime-matrix` (measure) for one target inside a
# Cloud Run Job with an attached L4 GPU. Driver on Cloud Run L4 is 535.x
# (CUDA 12.2), so the torch wheel is pinned to the cu121 build for
# compatibility rather than the newer cu124 build.

FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/workspace/.cache/huggingface

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip git \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

WORKDIR /workspace
COPY apps/api /workspace/apps/api

RUN python -m pip install --upgrade pip \
    && python -m pip install torch --index-url https://download.pytorch.org/whl/cu121 \
    && python -m pip install -e "/workspace/apps/api[retrieval]" \
    && python -m pip install huggingface_hub>=0.25 google-cloud-storage>=2.18

COPY infra/benchmarks/entrypoint.py /workspace/entrypoint.py

ENTRYPOINT ["python", "/workspace/entrypoint.py"]

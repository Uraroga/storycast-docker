FROM python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CUDA_VISIBLE_DEVICES="" \
    ATEN_CPU_CAPABILITY=default \
    ONEDNN_MAX_CPU_ISA=AVX \
    DNNL_MAX_CPU_ISA=AVX

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libsndfile1 \
        sox \
    && rm -rf /var/lib/apt/lists/*

COPY docker/base-tts/requirements.txt /tmp/requirements.txt
RUN python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.12.1+cpu
RUN python -m pip install --no-deps --index-url https://download.pytorch.org/whl/cpu torchaudio==2.11.0+cpu
RUN python -m pip install -r /tmp/requirements.txt \
    && python -m pip install --no-deps qwen-tts==0.1.1 \
    && rm /tmp/requirements.txt
RUN python -c "import qwen_tts, torch, torchaudio, transformers; print(torch.__version__, torchaudio.__version__, transformers.__version__)"

WORKDIR /workspace
LABEL org.storycast.base="tts-2026-08-25"
CMD ["python", "-c", "import sys; print('qwen3-tts CPU container OK'); print(sys.version)"]

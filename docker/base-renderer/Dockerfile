FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY docker/base-renderer/requirements.txt /tmp/requirements.txt
RUN python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.7.1 \
    && python -m pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && python -c "import PIL, torch, transformers; print(PIL.__version__, torch.__version__, transformers.__version__)"

WORKDIR /workspace
LABEL org.storycast.base="renderer-2026-08-25"
CMD ["python", "-c", "import sys; print('renderer CPU container OK'); print(sys.version)"]

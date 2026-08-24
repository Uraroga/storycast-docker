#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET_ROOT="${STORYCAST_MODELS_HOST:-$PROJECT_DIR/models}"
MODEL_NAME="Qwen3-TTS-12Hz-1.7B-CustomVoice"

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "ERRORE: Docker non è disponibile o il daemon non è raggiungibile (non viene usato sudo)." >&2
  exit 1
fi
mkdir -p "$TARGET_ROOT"
echo "[Storycast] Download esplicito di Qwen/$MODEL_NAME in $TARGET_ROOT/$MODEL_NAME"
echo "[Storycast] Sono necessari Internet, circa 4,2 GiB di download e spazio aggiuntivo."
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -e HF_HOME=/tmp/huggingface \
  -v "$TARGET_ROOT:/models" \
  python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7 \
  sh -ec 'python -m pip install --quiet --no-cache-dir huggingface-hub==0.36.2 && hf download Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice --local-dir /models/Qwen3-TTS-12Hz-1.7B-CustomVoice'
echo "[Storycast] Modello scaricato. Eseguire ./build-storycast.sh"

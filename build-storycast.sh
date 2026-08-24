#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

for command_name in docker; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "ERRORE: $command_name non è disponibile." >&2
    exit 1
  fi
done
if ! docker info >/dev/null 2>&1; then
  echo "ERRORE: Docker non è raggiungibile. Avvia il daemon e riprova (lo script non usa sudo)." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERRORE: il plugin Docker Compose v2 non è disponibile." >&2
  exit 1
fi

build_if_missing() {
  local image="$1" dockerfile="$2" expected_label="$3" current_label=""
  current_label="$(docker image inspect --format '{{ index .Config.Labels "org.storycast.base" }}' "$image" 2>/dev/null || true)"
  if [[ "${STORYCAST_FORCE_BASE_BUILD:-0}" != "1" && "$current_label" == "$expected_label" ]]; then
    echo "[Storycast] Immagine base verificata: $image ($current_label)"
  else
    echo "[Storycast] Costruzione immagine base: $image"
    docker build --pull -f "$dockerfile" -t "$image" .
  fi
}

echo "[Storycast] La build può usare Internet per immagini e pacchetti; il modello AI non viene scaricato."
if [[ "${STORYCAST_FORCE_BASE_BUILD:-0}" == "1" ]]; then
  echo "[Storycast] Ricostruzione esplicita delle immagini base richiesta."
fi
build_if_missing qwen3-tts-cpu:local docker/base-tts/Dockerfile tts-2026-08-25
build_if_missing voiceover-to-video:local docker/base-renderer/Dockerfile renderer-2026-08-25

echo "[Storycast] Costruzione immagini applicative"
docker compose build storycast-controller storycast-tts storycast-renderer
docker compose config --quiet

model_root="${STORYCAST_MODELS_HOST:-$PROJECT_DIR/models}"
model_dir="$model_root/Qwen3-TTS-12Hz-1.7B-CustomVoice"
required_model_files=(config.json model.safetensors speech_tokenizer/config.json speech_tokenizer/model.safetensors)
missing=()
for relative in "${required_model_files[@]}"; do
  [[ -s "$model_dir/$relative" ]] || missing+=("$relative")
done
if ((${#missing[@]})); then
  echo "[Storycast] Build completata. Modello non pronto in: $model_dir" >&2
  printf '[Storycast] File mancante: %s\n' "${missing[@]}" >&2
  echo "[Storycast] Consultare docs/MODELLI.md prima della generazione reale." >&2
  exit 2
else
  echo "[Storycast] Modello verificato: $model_dir"
fi
echo "[Storycast] Immagini pronte. Eseguire: ./avvia-storycast.sh precheck input/MODELLO_DIALOGO.txt"

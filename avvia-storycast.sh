#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="storycast-controller:local"
usage() {
  cat <<'EOF'
Uso: ./avvia-storycast.sh COMANDO [OPZIONI]

Comandi principali:
  genera [INPUT] [--nome SLUG] [--story-images ask|yes|no] [--review-audio] [--velocita FATTORE|--no-speed-version]
  precheck [INPUT] [--nome SLUG]
  short-audio [INPUT] --nome SLUG [--mock]
  short-video [INPUT] --nome SLUG [--mock]
  short-status|short-list-segments [INPUT] --nome SLUG [--mock]
  short-ascolta --nome SLUG
  short-video-play --nome SLUG
  piano --input INPUT [--nome SLUG] [--dry-run]
  stato|segmenti|riprendi|verifica --nome SLUG
  ascolta --nome SLUG --indice INDEX
  cpu-cooldown-status|cpu-cooldown-check [--nome SLUG]
  cpu-cooldown-plan|diagnostica-segmento|verifica-segmenti|segmenti-sospetti --nome SLUG
  ripara-segmento|ripara-sospetti --nome SLUG [--indice INDEX] [--dry-run|--yes]
  approva|rifiuta --nome SLUG --indice INDEX
  rigenera --nome SLUG --indice INDEX [--alternate-seed|--prudent]
  pulisci --nome SLUG --dry-run
  elimina-storia --nome SLUG --dry-run|--yes
  azzera-lavori --dry-run|--yes
  spazio-lavori
  work-status [--details]
  clean-work --dry-run|--yes
  logs|log-last
  status|validate|parse|timeline|check|test
  tts-status|tts-check|tts-plan|tts-generate|tts-regenerate|tts-verify
  tts-instruction-status|tts-instruction-profile [PROFILO]
  tts-instruction-ab-test [--dry-run]|tts-instruction-ab-test-status
  audio-merge|audio-status
  visual-status|visual-check|visual-plan|visual-assets|visual-verify
  visual-library-status|visual-library-check
  visual-library-plan|visual-library-build [--dry-run]
  episode-01-render-library [--dry-run]
  visual-library-clean --dry-run
  render-test|render-status|render-check
  test-face-animation [--image PNG --audio WAV --output MP4] [--dry-run]
  episode-01-plan|episode-01-tts|episode-01-audio|episode-01-status
  episode-01-audio-review [--rebuild]
  episode-01-list-segments
  episode-01-play-segment INDEX
  episode-01-segment-status INDEX
  episode-01-regenerate-segment INDEX [--dry-run|--alternate-seed|--prudent]
  episode-01-approve-segment INDEX
  episode-01-reject-segment INDEX
  episode-01-review-status
  episode-01-audio-qc [--strict]
  episode-01-migrate-metadata [--dry-run]
  episode-01-qc-status
  episode-01-rebuild-after-segment INDEX
  episode-01-visual|episode-01-render|episode-01-check|episode-01-build
  *-clean --dry-run (la cancellazione richiede sempre --yes)
Il controller usa Docker, non installa dipendenze sull'host e non usa la rete.
EOF
}
case "${1:-help}" in
  help|-h|--help) usage; exit 0 ;;
  logs)
    find "$PROJECT_DIR/logs" -maxdepth 1 -type f -name '*.log' ! -name 'latest.log' -printf '%TY-%Tm-%Td %TH:%TM:%TS  %9s  %f\n' | sort -r | head -20
    exit 0 ;;
  log-last)
    [[ -e "$PROJECT_DIR/logs/latest.log" ]] || { echo "Nessun log Storycast disponibile." >&2; exit 1; }
    exec sed -n '1,240p' "$PROJECT_DIR/logs/latest.log" ;;
  clean-work|work-status)
    command="$1"; shift
    if ! docker image inspect storycast-controller:local >/dev/null 2>&1; then echo "Immagine storycast-controller:local assente." >&2; exit 3; fi
    exec docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-controller -u -m storycast.work_manager "$command" "$@"
    ;;
  status|validate|parse|timeline|check|test) command="$1"; service="storycast-controller"; shift ;;
  episode-01-play-segment)
    [[ "${2:-}" =~ ^[0-9]+$ ]] || { echo "Indice numerico richiesto." >&2; exit 2; }
    wav="$(find "$PROJECT_DIR/work/episode_01/audio_segments" -maxdepth 1 -type f -name "$(printf '%04d' "$2")_*.wav" -print -quit)"
    [[ -n "$wav" ]] || { echo "Segmento inesistente: $2" >&2; exit 1; }
    if command -v ffplay >/dev/null 2>&1; then exec ffplay -nodisp -autoexit "$wav"; fi
    echo "$wav"; exit 0 ;;
  ascolta)
    shift; slug=""; index=""
    while (($#)); do
      case "$1" in
        --nome) slug="${2:-}"; shift 2 ;;
        --indice) index="${2:-}"; shift 2 ;;
        *) echo "Opzione ascolta sconosciuta: $1" >&2; exit 2 ;;
      esac
    done
    [[ "$slug" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo "Slug non valido o mancante." >&2; exit 2; }
    [[ "$index" =~ ^[0-9]+$ ]] || { echo "Indice numerico richiesto." >&2; exit 2; }
    wav="$(find "$PROJECT_DIR/work/episodes/$slug/audio_segments" -maxdepth 1 -type f -name "$(printf '%04d' "$index")_*.wav" -print -quit 2>/dev/null || true)"
    [[ -n "$wav" ]] || { echo "Segmento inesistente: $slug/$index" >&2; exit 1; }
    if command -v ffplay >/dev/null 2>&1; then exec ffplay -nodisp -autoexit "$wav"; fi
    echo "$wav"; exit 0 ;;
  short-ascolta)
    shift; slug=""
    while (($#)); do
      case "$1" in
        --nome) slug="${2:-}"; shift 2 ;;
        *) echo "Opzione short-ascolta sconosciuta: $1" >&2; exit 2 ;;
      esac
    done
    [[ "$slug" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo "Slug non valido o mancante." >&2; exit 2; }
    wav="$PROJECT_DIR/output/$slug/${slug}_short_audio.wav"
    [[ -f "$wav" ]] || { echo "Audio Short inesistente: $wav" >&2; exit 1; }
    if command -v ffplay >/dev/null 2>&1; then exec ffplay -nodisp -autoexit "$wav"; fi
    echo "$wav"; exit 0 ;;
  short-video-play)
    shift; slug=""
    while (($#)); do
      case "$1" in
        --nome) slug="${2:-}"; shift 2 ;;
        *) echo "Opzione short-video-play sconosciuta: $1" >&2; exit 2 ;;
      esac
    done
    [[ "$slug" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo "Slug non valido o mancante." >&2; exit 2; }
    video="$PROJECT_DIR/output/$slug/${slug}_short_video.mp4"
    [[ -f "$video" ]] || { echo "Video Short inesistente: $video" >&2; exit 1; }
    if command -v ffplay >/dev/null 2>&1; then exec ffplay -autoexit "$video"; fi
    echo "$video"; exit 0 ;;
  genera|precheck|short-audio|short-video|short-status|short-list-segments|stato|piano|segmenti|approva|rifiuta|rigenera|riprendi|verifica|pulisci|cpu-cooldown-status|cpu-cooldown-check|cpu-cooldown-plan|diagnostica-segmento|verifica-segmenti|segmenti-sospetti|ripara-segmento|ripara-sospetti)
    command="$1"; shift
    if ! docker image inspect storycast-tts:local >/dev/null 2>&1; then echo "Immagine storycast-tts:local assente." >&2; exit 3; fi
    exec docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-tts -u -m storycast.orchestrator "$command" "$@"
    ;;
  elimina-storia|azzera-lavori|spazio-lavori)
    command="$1"; shift
    active="$(docker compose -f "$PROJECT_DIR/docker-compose.yml" ps -q --status running)"
    [[ -z "$active" ]] || { echo "ERRORE: container Storycast attivi; attendere la conclusione senza terminarli." >&2; exit 1; }
    if ! docker image inspect storycast-controller:local >/dev/null 2>&1; then echo "Immagine storycast-controller:local assente." >&2; exit 3; fi
    exec docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm storycast-cleanup "$command" "$@"
    ;;
  tts-instruction-status|tts-instruction-profile|tts-instruction-ab-test|tts-instruction-ab-test-status)
    command="$1"; shift
    if ! docker image inspect storycast-tts:local >/dev/null 2>&1; then echo "Immagine storycast-tts:local assente." >&2; exit 3; fi
    exec docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-tts -m storycast.instruction_profiles "$command" "$@"
    ;;
  tts-status|tts-check|tts-plan|tts-generate|tts-regenerate|tts-verify|audio-merge|audio-status|tts-real-test|tts-real-test-status|tts-real-test-cache-check|tts-real-test-merge|tts-real-test-clean|episode-01-plan|episode-01-tts|episode-01-audio|episode-01-status|episode-01-clean|episode-01-audio-review|episode-01-list-segments|episode-01-segment-status|episode-01-regenerate-segment|episode-01-audio-qc|episode-01-qc-status|episode-01-migrate-metadata|episode-01-approve-segment|episode-01-reject-segment|episode-01-review-status)
    command="$1"; service="storycast-tts"; shift ;;
  episode-01-rebuild-after-segment)
    command="$1"; shift
    if ! docker image inspect storycast-renderer:local >/dev/null 2>&1; then echo "Immagine storycast-renderer:local assente." >&2; exit 3; fi
    exec docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-renderer -m storycast.tts_cli "$command" "$@"
    ;;
  test-face-animation)
    shift
    if ! docker image inspect storycast-renderer:local >/dev/null 2>&1; then
      echo "Immagine storycast-renderer:local assente. Crearla con: ./build-storycast.sh" >&2
      exit 3
    fi
    exec docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-renderer /app/scripts/face_animation_poc.py "$@"
    ;;
  visual-status|visual-check|visual-plan|visual-assets|visual-verify|render-test|render-status|render-check|visual-clean|episode-01-visual|episode-01-render|episode-01-check|visual-library-status|visual-library-check|visual-library-plan|visual-library-build|visual-library-clean|episode-01-render-library)
    command="$1"; service="storycast-renderer"; shift ;;
  episode-01-build)
    shift
    if [[ "${1:-}" == "--dry-run" ]]; then exec "$PROJECT_DIR/avvia-storycast.sh" episode-01-tts --dry-run; fi
    "$PROJECT_DIR/avvia-storycast.sh" episode-01-tts
    "$PROJECT_DIR/avvia-storycast.sh" episode-01-audio
    "$PROJECT_DIR/avvia-storycast.sh" episode-01-visual
    "$PROJECT_DIR/avvia-storycast.sh" episode-01-render
    exec "$PROJECT_DIR/avvia-storycast.sh" episode-01-check ;;
  *) echo "Comando sconosciuto: $1" >&2; usage >&2; exit 2 ;;
esac
required_image="$IMAGE_NAME"
[[ "$service" == "storycast-tts" ]] && required_image="storycast-tts:local"
[[ "$service" == "storycast-renderer" ]] && required_image="storycast-renderer:local"
if ! docker image inspect "$required_image" >/dev/null 2>&1; then
  echo "Immagine $required_image assente. Crearla con: ./build-storycast.sh" >&2; exit 3
fi
if [[ "$command" == "test" ]]; then
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-controller -m unittest discover -s tests -p "test_storycast.py" -v
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-controller -m unittest discover -s tests -p "test_audio_qc.py" -v
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-controller -m unittest discover -s tests -p "test_instruction_profiles.py" -v
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-controller -m unittest discover -s tests -p "test_cleanup.py" -v
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-controller -m unittest discover -s tests -p "test_run_logging.py" -v
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-controller -m unittest discover -s tests -p "test_work_manager.py" -v
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-controller -m unittest discover -s tests -p "test_episode_bundle.py" -v
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-renderer -m unittest discover -s tests -p "test_story_images.py" -v
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-controller -m unittest discover -s tests -p "test_short_pipeline.py" -v
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-renderer -m unittest discover -s tests -p "test_short_video.py" -v
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-renderer -m unittest discover -s tests -p "test_final_speed.py" -v
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-renderer -m unittest discover -s tests -p "test_visual.py" -v
  docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-renderer -m unittest discover -s tests -p "test_visual_library.py" -v
  exec docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm --entrypoint python storycast-renderer -m unittest discover -s tests -p "test_orchestrator.py" -v
fi
exec docker compose -f "$PROJECT_DIR/docker-compose.yml" run --rm "$service" "$command" "$@"

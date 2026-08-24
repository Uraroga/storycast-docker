from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import Character, StorycastError, load_characters, parse_dialogue, read_utf8


@dataclass(frozen=True)
class EpisodeBundle:
    """Input validati e configurazione delle due fasi sequenziali."""

    main_path: Path
    short_path: Path
    main_entries: list[dict[str, Any]]
    short_entries: list[dict[str, Any]]
    required_characters: tuple[str, ...]


def associated_short_path(main_path: Path) -> Path:
    """Deriva lo Short conservando cartella, basename e maiuscole dell'episodio."""
    if main_path.suffix.lower() != ".txt":
        raise StorycastError(f"Il file principale deve avere estensione .txt: {main_path}")
    if main_path.stem.endswith("-short"):
        raise StorycastError("Come input principale usare l'episodio, non il file -short.txt")
    return main_path.with_name(f"{main_path.stem}-short{main_path.suffix}")


def load_pipeline_config(root: Path) -> dict[str, Any]:
    path = root / "config" / "pipeline.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StorycastError(f"Configurazione pipeline mancante: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorycastError(f"Configurazione pipeline non valida: {path}: {exc}") from exc
    required = data.get("precheck", {}).get("required_characters")
    if not isinstance(required, list) or not required or not all(isinstance(x, str) and x for x in required):
        raise StorycastError("config/pipeline.json: precheck.required_characters deve essere una lista non vuota")
    if len(set(required)) != len(required):
        raise StorycastError("config/pipeline.json: personaggi obbligatori duplicati")
    return data


def _validate_dialogue(path: Path, characters: dict[str, Character], required: tuple[str, ...]) -> list[dict[str, Any]]:
    if not path.is_file():
        raise StorycastError(f"File input mancante: {path}")
    if not read_utf8(path).strip():
        raise StorycastError(f"File input vuoto: {path}")
    entries = parse_dialogue(path, characters)
    counts = {character: 0 for character in required}
    for entry in entries:
        if entry["speaker"] in counts:
            counts[entry["speaker"]] += 1
    missing = [character for character, count in counts.items() if count == 0]
    if missing:
        raise StorycastError(
            f"Dialogo {path} senza almeno una battuta valida per: {', '.join(missing)}"
        )
    return entries


def precheck_episode_bundle(root: Path, main_path: Path) -> EpisodeBundle:
    """Valida entrambi gli input prima che siano create cache o avviato il TTS."""
    if not main_path.is_file():
        raise StorycastError(f"File input mancante: {main_path}")
    if not read_utf8(main_path).strip():
        raise StorycastError(f"File input vuoto: {main_path}")
    short_path = associated_short_path(main_path)
    if not short_path.is_file():
        try:
            shown = short_path.relative_to(root).as_posix()
        except ValueError:
            shown = str(short_path)
        raise StorycastError(f"Short associato non trovato:\n{shown}")
    config = load_pipeline_config(root)
    required = tuple(config["precheck"]["required_characters"])
    characters = load_characters(root)
    unknown = [character for character in required if character not in characters]
    if unknown:
        raise StorycastError(f"Personaggi obbligatori non configurati: {', '.join(unknown)}")
    main_entries = _validate_dialogue(main_path, characters, required)
    short_entries = _validate_dialogue(short_path, characters, required)
    return EpisodeBundle(main_path, short_path, main_entries, short_entries, required)


def pipeline_plan(root: Path, slug: str, bundle: EpisodeBundle) -> dict[str, Any]:
    """Descrive il pacchetto completo senza eseguire TTS o rendering."""
    output = root / "output" / slug
    return {
        "execution": "sequential",
        "phases": ["precheck", "main_episode", "main_checks", "short_audio", "short_video", "final_checks"],
        "main_episode": {
            "input": bundle.main_path.relative_to(root).as_posix(),
            "status": "ready",
            "outputs": [
                (output / f"{slug}_video.mp4").relative_to(root).as_posix(),
                (output / f"{slug}_audio.wav").relative_to(root).as_posix(),
            ],
        },
        "short": {
            "input": bundle.short_path.relative_to(root).as_posix(),
            "status": "ready",
            "outputs": [
                (output / f"{slug}_short_video.mp4").relative_to(root).as_posix(),
                (output / f"{slug}_short_audio.wav").relative_to(root).as_posix(),
                (output / f"{slug}_short_subtitles.srt").relative_to(root).as_posix(),
            ],
        },
    }

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class StorycastError(ValueError):
    """Errore leggibile dall'utente durante validazione o parsing."""


ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
HEADER_RE = re.compile(r"^\[([^\[\]\n]+)\]$")
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")


@dataclass(frozen=True)
class Character:
    id: str
    config_file: Path
    master_image: Path | None


def read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise StorycastError(f"File mancante: {path}") from exc
    except UnicodeDecodeError as exc:
        raise StorycastError(f"Codifica non UTF-8 in {path}: byte {exc.start}") from exc


def _top_level_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(read_utf8(path).splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#") or raw[:1].isspace():
            continue
        match = KEY_RE.match(raw)
        if not match:
            raise StorycastError(f"YAML non valido in {path}, riga {number}: {raw}")
        key, value = match.groups()
        if key in values:
            raise StorycastError(f"Chiave YAML duplicata '{key}' in {path}")
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise StorycastError(f"Stringa YAML non chiusa in {path}, riga {number}")
            value = value[1:-1]
        values[key] = value
    return values


def load_characters(project_root: Path) -> dict[str, Character]:
    config_dir = project_root / "config" / "characters"
    files = sorted(config_dir.glob("*.yaml")) if config_dir.is_dir() else []
    if not files:
        raise StorycastError(f"Nessuna configurazione YAML in {config_dir}")
    characters: dict[str, Character] = {}
    for path in files:
        values = _top_level_values(path)
        char_id = values.get("id", "").strip()
        image = values.get("immagine_principale", "").strip()
        if not char_id or not ID_RE.fullmatch(char_id):
            raise StorycastError(f"Identificatore mancante o non valido in {path}")
        if char_id in characters:
            raise StorycastError(
                f"Identificatore duplicato '{char_id}' in {characters[char_id].config_file} e {path}"
            )
        image_path = None
        if image:
            image_path = (project_root / image).resolve()
            try:
                image_path.relative_to(project_root.resolve())
            except ValueError as exc:
                raise StorycastError(f"Percorso immagine fuori progetto in {path}: {image}") from exc
            # immagine_principale è un riferimento editoriale opzionale. Le pose
            # usate dal video sono validate dal catalogo dinamico, non da questo file.
            if not image_path.is_file():
                image_path = None
        characters[char_id] = Character(char_id, path, image_path)
    return characters


def _parse_header(header: str, line: int) -> dict[str, Any]:
    parts = [part.strip() for part in header.split("|")]
    speaker = parts.pop(0) if parts else ""
    if not speaker or not ID_RE.fullmatch(speaker):
        raise StorycastError(f"Speaker non valido alla riga {line}: '{speaker}'")
    result: dict[str, Any] = {"speaker": speaker, "emotion": None, "stage_direction": None, "pause": None}
    for part in parts:
        if not part:
            raise StorycastError(f"Campo vuoto nell'intestazione alla riga {line}")
        if "=" not in part:
            if result["emotion"] is not None:
                raise StorycastError(f"Emozione duplicata alla riga {line}")
            result["emotion"] = part
            continue
        key, value = (item.strip() for item in part.split("=", 1))
        if not value:
            raise StorycastError(f"Valore vuoto per '{key}' alla riga {line}")
        if key in {"stage", "scena"}:
            if result["stage_direction"] is not None:
                raise StorycastError(f"Indicazione scenica duplicata alla riga {line}")
            result["stage_direction"] = value
        elif key in {"pause", "pausa"}:
            try:
                pause = float(value.replace(",", "."))
            except ValueError as exc:
                raise StorycastError(f"Pausa non numerica alla riga {line}: '{value}'") from exc
            if pause < 0:
                raise StorycastError(f"Pausa negativa alla riga {line}")
            result["pause"] = pause
        else:
            raise StorycastError(f"Opzione sconosciuta '{key}' alla riga {line}")
    return result


def parse_dialogue(path: Path, characters: dict[str, Character]) -> list[dict[str, Any]]:
    lines = read_utf8(path).splitlines()
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    text_lines: list[str] = []

    def finish() -> None:
        nonlocal current, text_lines
        if current is None:
            return
        text = "\n".join(text_lines).strip()
        if not text:
            raise StorycastError(f"Battuta vuota dopo l'intestazione alla riga {current['line']}")
        index = len(entries) + 1
        entries.append({
            "index": index,
            "speaker": current["speaker"],
            "text": text,
            "emotion": current["emotion"],
            "stage_direction": current["stage_direction"],
            "pause": current["pause"],
            "audio_file": f"work/audio_segments/{index:04d}_{current['speaker']}.wav",
            "status": "pending",
        })
        current, text_lines = None, []

    for number, raw in enumerate(lines, 1):
        stripped = raw.strip()
        match = HEADER_RE.fullmatch(stripped)
        if stripped.startswith("[") or stripped.endswith("]"):
            if not match:
                raise StorycastError(f"Sintassi intestazione errata alla riga {number}: {raw}")
        if match:
            finish()
            current = _parse_header(match.group(1), number)
            current["line"] = number
            if current["speaker"] not in characters:
                raise StorycastError(f"Personaggio inesistente alla riga {number}: {current['speaker']}")
        elif current is None:
            if stripped and not stripped.startswith("#"):
                raise StorycastError(f"Testo senza intestazione alla riga {number}: {raw}")
        else:
            text_lines.append(raw)
    finish()
    if not entries:
        raise StorycastError(f"Nessuna battuta valida in {path}")
    return entries


def make_timeline(entries: list[dict[str, Any]], characters: dict[str, Character], project_root: Path) -> list[dict[str, Any]]:
    timeline = []
    for entry in entries:
        master = characters[entry["speaker"]].master_image
        image = master.relative_to(project_root).as_posix() if master else None
        timeline.append({
            "index": entry["index"], "speaker": entry["speaker"], "text": entry["text"],
            "emotion": entry["emotion"], "stage_direction": entry["stage_direction"],
            "pause": entry["pause"], "audio_file": entry["audio_file"],
            "start": None, "end": None, "duration": None,
            "shot_type": "medium_closeup", "visual_asset": image, "status": "pending",
        })
    return timeline


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_json_atomic(path: Path, data: Any) -> None:
    """Scrive JSON durevole senza esporre al lettore un file parziale."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        tmp.unlink(missing_ok=True)

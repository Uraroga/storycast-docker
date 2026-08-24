from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

from .core import StorycastError
from .tts import file_hash
from .visual import image_size, load_json_config


def _story_image_size(path: Path) -> tuple[int, int]:
    if path.suffix.lower() != ".webp":
        return image_size(path)
    data = path.read_bytes()[:32]
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise StorycastError(f"WEBP non valido: {path}")
    kind = data[12:16]
    if kind == b"VP8X":
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        return int.from_bytes(data[26:28], "little") & 0x3fff, int.from_bytes(data[28:30], "little") & 0x3fff
    if kind == b"VP8L" and data[20] == 0x2f:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
    raise StorycastError(f"WEBP non supportato o corrotto: {path}")


def load_story_image_config(root: Path) -> dict[str, Any]:
    cfg = load_json_config(root / "config/render.yaml").get("story_images", {})
    required = ("directory", "extensions", "insert_seconds", "fade_seconds",
                "opening_guard_seconds", "closing_guard_seconds",
                "target_interval_seconds", "max_zoom")
    missing = [key for key in required if key not in cfg]
    if missing:
        raise StorycastError(f"Configurazione immagini storia incompleta: {', '.join(missing)}")
    return cfg


def discover_story_images(root: Path) -> list[dict[str, Any]]:
    cfg = load_story_image_config(root)
    folder = root / cfg["directory"]
    if not folder.is_dir():
        return []
    extensions = {str(value).lower() for value in cfg["extensions"]}
    rows = []
    for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        try:
            width, height = _story_image_size(path)
        except (OSError, StorycastError):
            continue
        rows.append({"path": path.relative_to(root).as_posix(), "sha256": file_hash(path),
                     "resolution": [width, height]})
    return rows


def story_images_signature(images: list[dict[str, Any]], enabled: bool, cfg: dict[str, Any] | None = None) -> str:
    payload = {"enabled": enabled, "images": images if enabled else [], "config": cfg or {}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def choose_story_images(root: Path, mode: str, *, input_fn: Callable[[str], str] = input,
                        interactive: bool | None = None) -> dict[str, Any]:
    if mode not in {"ask", "yes", "no"}:
        raise StorycastError("--story-images accetta ask, yes oppure no")
    images = discover_story_images(root)
    count = len(images)
    folder = load_story_image_config(root)["directory"] + "/"
    if count:
        print(f"[storycast] immagini storia: trovate {count} immagini in {folder}", flush=True)
    else:
        print(f"[storycast] immagini storia: nessuna immagine trovata in {folder}", flush=True)
    if mode == "yes":
        enabled = bool(images)
    elif mode == "no":
        enabled = False
    else:
        is_interactive = sys.stdin.isatty() if interactive is None else interactive
        if is_interactive:
            prompt = "Vuoi usarle nel video principale? [S/n] " if images else "Continuare senza immagini aggiuntive? [S/n] "
            answer = input_fn(prompt).strip().lower()
            if not images and answer in {"n", "no"}:
                raise StorycastError("Generazione annullata dall'utente")
            enabled = bool(images) and answer not in {"n", "no"}
        else:
            enabled = False
            print("[storycast] immagini storia: modalità non interattiva, continuazione senza immagini aggiuntive", flush=True)
    print("[storycast] immagini storia: uso confermato" if enabled else
          "[storycast] continuazione senza immagini aggiuntive", flush=True)
    return {"mode": mode, "enabled": enabled, "images": images if enabled else [],
            "found": count, "signature": story_images_signature(images, enabled, load_story_image_config(root))}


def plan_story_inserts(total_duration: float, images: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    if not images:
        return []
    opening = float(cfg["opening_guard_seconds"]); closing = float(cfg["closing_guard_seconds"])
    duration = float(cfg["insert_seconds"]); interval = float(cfg["target_interval_seconds"])
    start, end = opening, total_duration - closing - duration
    if end < start:
        return []
    capacity = max(1, int((end - start) // interval) + 1)
    count = min(len(images), capacity)
    positions = [start + (end - start) * (index + 1) / (count + 1) for index in range(count)]
    return [{"index": index + 1, "start": round(position, 6), "end": round(position + duration, 6),
             "duration": duration, "image": images[index % len(images)]}
            for index, position in enumerate(positions)]


def apply_story_inserts(plan: dict[str, Any], images: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    inserts = plan_story_inserts(float(plan["total_duration"]), images, cfg)
    if not inserts:
        return {**plan, "story_images": [], "story_images_signature": story_images_signature(images, bool(images), cfg)}
    scenes = []
    for scene in plan["scenes"]:
        cursor = float(scene["start"])
        overlaps = [item for item in inserts if item["start"] < scene["end"] and item["end"] > scene["start"]]
        for item in overlaps:
            cut = max(cursor, item["start"])
            if cut > cursor:
                scenes.append({**scene, "start": cursor, "end": cut, "duration": cut - cursor})
            if float(scene["start"]) <= item["start"] < float(scene["end"]):
                image = item["image"]
                scenes.append({"start": item["start"], "end": item["end"], "duration": item["duration"],
                               "speaker": None, "source_asset": image["path"], "derived_asset": image["path"],
                               "asset_id": f"story_image_{item['index']:02d}", "pose_type": "story_image",
                               "movement": "story_slow_zoom", "transition": "fade", "reason": "inserto immagine storia",
                               "story_image": True, "source_sha256": image["sha256"]})
            cursor = max(cursor, item["end"])
        if cursor < float(scene["end"]):
            scenes.append({**scene, "start": cursor, "end": scene["end"], "duration": float(scene["end"]) - cursor})
    scenes = [scene for scene in scenes if scene["duration"] > 1e-6]
    for index, scene in enumerate(scenes, 1):
        scene["index"] = index
    return {**plan, "scenes": scenes, "story_images": inserts,
            "story_images_signature": story_images_signature(images, True, cfg)}

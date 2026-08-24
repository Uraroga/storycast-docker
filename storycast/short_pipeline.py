from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import StorycastError, load_characters, write_json_atomic
from .episode_bundle import EpisodeBundle, precheck_episode_bundle
from .tts import (active_instruction_profile, build_plan, cache_status, file_hash,
                  generate, load_tts_config, load_voices, merge_audio, verify_plan,
                  wav_info)


def short_paths(root: Path, slug: str) -> dict[str, Path]:
    work = root / "work" / "episodes" / slug / "short"
    output = root / "output" / slug
    return {
        "work": work,
        "segments": work / "audio_segments",
        "segment_metadata": work / "metadata" / "audio_segments",
        "plan": work / "metadata" / "tts_plan.json",
        "state": work / "state.json",
        "manifest": output / f"{slug}_short_audio_manifest.json",
        "audio": output / f"{slug}_short_audio.wav",
        "timeline": output / f"{slug}_short_timeline.json",
        "subtitles": output / f"{slug}_short_subtitles.srt",
        "video": output / f"{slug}_short_video.mp4",
    }


def _short_context(root: Path, slug: str, bundle: EpisodeBundle, mock: bool) -> tuple[dict, list[dict], dict]:
    characters = load_characters(root)
    profile = active_instruction_profile(root)
    voices = load_voices(root, characters, instruction_profile=profile)
    config = dict(load_tts_config(root))
    config["backend"] = "mock" if mock else config["backend"]
    used = {entry["speaker"] for entry in bundle.short_entries}
    available = set(config["verification"].get("available_voices", []))
    for speaker in used:
        voice = voices.get(speaker)
        if not voice:
            raise StorycastError(f"Speaker Short senza voce configurata: {speaker}")
        if voice["voice"] not in available:
            raise StorycastError(f"Voce Short non disponibile nel modello per {speaker}: {voice['voice']}")
    plan = build_plan(root, bundle.short_entries, voices, config)
    paths = short_paths(root, slug)
    for item in plan:
        stem = f"{item['index']:04d}_{item['speaker']}"
        item["wav_path"] = paths["segments"] / f"{stem}.wav"
        item["metadata_path"] = paths["segment_metadata"] / f"{stem}.json"
        item["cache_status"] = cache_status(item)
        item["namespace"] = "short"
    return config, plan, voices


def _jsonable_plan(plan: list[dict]) -> list[dict]:
    return [{key: value.as_posix() if isinstance(value, Path) else value for key, value in item.items()}
            for item in plan]


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorycastError(f"Stato Short non valido: {path}: {exc}") from exc
    return data if isinstance(data, dict) else None


def short_status(root: Path, main_path: Path, slug: str, mock: bool = False) -> dict[str, Any]:
    bundle = precheck_episode_bundle(root, main_path)
    config, plan, voices = _short_context(root, slug, bundle, mock)
    paths = short_paths(root, slug)
    checks = verify_plan(plan, config)
    valid = sum(item["status"] == "valid" for item in checks)
    state = _load_state(paths["state"])
    audio = wav_info(paths["audio"]) if paths["audio"].is_file() else None
    return {
        "slug": slug,
        "namespace": "short",
        "status": state.get("status", "audio_pending") if state else "audio_pending",
        "backend": config["backend"],
        "input": bundle.short_path.relative_to(root).as_posix(),
        "segments": len(plan),
        "cache": {"valid": valid, "to_generate": len(plan) - valid},
        "characters": sorted({item["speaker"] for item in plan}),
        "voices": {speaker: voices[speaker]["voice"] for speaker in sorted({item["speaker"] for item in plan})},
        "audio": paths["audio"].relative_to(root).as_posix(),
        "timeline": paths["timeline"].relative_to(root).as_posix(),
        "audio_info": audio,
        "video_status": "not_implemented",
    }


def short_list_segments(root: Path, main_path: Path, slug: str, mock: bool = False) -> list[dict[str, Any]]:
    bundle = precheck_episode_bundle(root, main_path)
    config, plan, _ = _short_context(root, slug, bundle, mock)
    timeline_entries: dict[int, dict] = {}
    timeline_path = short_paths(root, slug)["timeline"]
    if timeline_path.is_file():
        data = json.loads(timeline_path.read_text(encoding="utf-8"))
        timeline_entries = {item["index"]: item for item in data.get("entries", [])}
    rows = []
    for item, check in zip(plan, verify_plan(plan, config)):
        timing = timeline_entries.get(item["index"], {})
        rows.append({
            "index": item["index"], "speaker": item["speaker"], "emotion": item["emotion"],
            "text": item["text"], "voice": item["voice"],
            "duration": timing.get("duration", check["audio"]["duration"] if check["audio"] else None),
            "start": timing.get("start"), "end": timing.get("end"),
            "wav": item["wav_path"].relative_to(root).as_posix(), "cache": cache_status(item),
        })
    return rows


def run_short_audio(root: Path, main_path: Path, slug: str, *, mock: bool = False,
                    announce_precheck: bool = True) -> dict[str, Any]:
    """Esegue esclusivamente TTS, merge e timeline dello Short già validato."""
    if announce_precheck:
        print("[storycast] precheck input", flush=True)
    bundle = precheck_episode_bundle(root, main_path)
    config, plan, voices = _short_context(root, slug, bundle, mock)
    paths = short_paths(root, slug)
    for key in ("segments", "segment_metadata"):
        paths[key].mkdir(parents=True, exist_ok=True)
    paths["audio"].parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(paths["plan"], {
        "schema_version": 1, "namespace": "short", "backend": config["backend"],
        "model": config["model"]["id"], "input": bundle.short_path.relative_to(root).as_posix(),
        "segments": _jsonable_plan(plan),
    })
    print("[storycast] short audio", flush=True)
    generated = generate(root, plan, config, config["backend"], state_path=paths["state"])
    for item in plan:
        item["cache_status"] = cache_status(item)
    invalid = [check["index"] for check in verify_plan(plan, config) if check["status"] != "valid"]
    if invalid:
        raise StorycastError(f"Segmenti Short mancanti o invalidi dopo TTS: {invalid}")
    cached_outputs = (generated["generated"] == 0 and paths["audio"].is_file()
                      and paths["timeline"].is_file() and paths["manifest"].is_file())
    if cached_outputs:
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        wav_info(paths["audio"])
    else:
        manifest = merge_audio(
            root, plan, config,
            output_rel=paths["audio"].relative_to(root).as_posix(),
            manifest_rel=paths["manifest"].relative_to(root).as_posix(),
            timeline_rel=paths["timeline"].relative_to(root).as_posix(),
            input_path=bundle.short_path,
        )
    timeline = json.loads(paths["timeline"].read_text(encoding="utf-8"))
    for entry, item in zip(timeline["entries"], plan):
        entry["namespace"] = "short"
        entry["cache_status"] = cache_status(item)
    timeline.update(namespace="short", status="audio_ready", source=bundle.short_path.relative_to(root).as_posix())
    write_json_atomic(paths["timeline"], timeline)
    state = {
        "schema_version": 1, "namespace": "short", "slug": slug, "status": "audio_ready",
        "video_status": "not_implemented", "input": bundle.short_path.relative_to(root).as_posix(),
        "input_hash": file_hash(bundle.short_path), "segments": len(plan),
        "segments_valid": [item["index"] for item in plan],
        "audio": paths["audio"].relative_to(root).as_posix(), "audio_hash": file_hash(paths["audio"]),
        "timeline": paths["timeline"].relative_to(root).as_posix(), "timeline_hash": file_hash(paths["timeline"]),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json_atomic(paths["state"], state)
    return {
        **state, "backend": config["backend"], "tts": generated,
        "duration": manifest["total_duration"],
        "voices": {speaker: voices[speaker]["voice"] for speaker in sorted({item["speaker"] for item in plan})},
        "cache": {"valid": sum(cache_status(item) == "valid" for item in plan),
                  "generated": generated["generated"], "cached": generated["cached"]},
    }

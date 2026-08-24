from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import shutil
import struct
import tempfile
import time
import warnings
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import StorycastError, load_characters, make_timeline, parse_dialogue, read_utf8, write_json
from .audio_safety import CpuCooldown, QC_SCHEMA_VERSION, technical_qc, validate_cpu_cooldown


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(read_utf8(path))
    except json.JSONDecodeError as exc:
        raise StorycastError(f"Configurazione non valida in {path}: riga {exc.lineno}, colonna {exc.colno}") from exc
    if not isinstance(value, dict):
        raise StorycastError(f"Configurazione non oggetto in {path}")
    return value


def load_tts_config(root: Path) -> dict[str, Any]:
    config = load_json(root / "config/tts.json")
    for key in ("backend", "model", "audio", "text", "verification"):
        if key not in config:
            raise StorycastError(f"Sezione '{key}' mancante in config/tts.json")
    validate_cpu_cooldown(config)
    return config


def load_instruction_configuration(root: Path, path: Path | None = None) -> dict[str, Any]:
    data = load_json(path or root / "config/voices.yaml")
    profiles = data.get("instruction_profiles")
    if profiles is None:  # schema 1: compatibilita' con configurazioni e storie legacy
        profiles = {"italian_legacy": {"instruction_language": "Italian", "spoken_language": "Italian",
                                        "emotion_template": " Emozione richiesta: {emotion}.",
                                        "unknown_emotion_value": "neutra"}}
        data["instruction_profile"] = "italian_legacy"
        data["emotion_mappings"] = {"italian_legacy": {}}
    if not isinstance(profiles, dict) or not profiles:
        raise StorycastError("instruction_profiles mancante o non valido in config/voices.yaml")
    for name, profile in profiles.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name) or not isinstance(profile, dict):
            raise StorycastError(f"Profilo istruzioni non valido: {name}")
        if profile.get("instruction_language") not in {"English", "Italian"}:
            raise StorycastError(f"instruction_language non valida nel profilo {name}")
        if profile.get("spoken_language") != "Italian":
            raise StorycastError(f"spoken_language deve rimanere Italian nel profilo {name}")
        if "{emotion}" not in str(profile.get("emotion_template", "")):
            raise StorycastError(f"emotion_template non valida nel profilo {name}")
    if data.get("instruction_profile") not in profiles:
        raise StorycastError("instruction_profile predefinito inesistente")
    data["instruction_profiles"] = profiles
    return data


def active_instruction_profile(root: Path, data: dict[str, Any] | None = None) -> str:
    data = data or load_instruction_configuration(root)
    override = root / "work/settings/tts_instruction_profile.json"
    selected = data["instruction_profile"]
    if override.is_file():
        value = load_json(override).get("instruction_profile")
        if value not in data["instruction_profiles"]:
            raise StorycastError(f"Profilo istruzioni configurato ma inesistente: {value}")
        selected = value
    return selected


def set_instruction_profile(root: Path, profile: str) -> dict[str, Any]:
    data = load_instruction_configuration(root)
    if profile not in data["instruction_profiles"]:
        raise StorycastError(f"Profilo inesistente: {profile}; disponibili: {', '.join(sorted(data['instruction_profiles']))}")
    target = root / "work/settings/tts_instruction_profile.json"
    payload = {"schema_version": 1, "instruction_profile": profile,
               "updated_at": datetime.now(timezone.utc).isoformat()}
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2); handle.write("\n")
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return payload


def load_voices(root: Path, characters: dict | None = None, path: Path | None = None,
                instruction_profile: str | None = None, legacy_hash_compat: bool = False) -> dict[str, dict[str, Any]]:
    characters = characters or load_characters(root)
    data = load_instruction_configuration(root, path)
    selected = instruction_profile or active_instruction_profile(root, data)
    if selected not in data["instruction_profiles"]:
        raise StorycastError(f"Profilo istruzioni inesistente: {selected}")
    profile = data["instruction_profiles"][selected]
    voices = data.get("voices")
    if not isinstance(voices, dict) or not voices:
        raise StorycastError("Nessuna voce definita in config/voices.yaml")
    result = {}
    for key, voice in voices.items():
        if not isinstance(voice, dict):
            raise StorycastError(f"Voce non valida per '{key}'")
        char_id = voice.get("character_id")
        if key != char_id:
            raise StorycastError(f"Chiave voce '{key}' diversa da character_id '{char_id}'")
        if char_id not in characters:
            raise StorycastError(f"Voce associata a personaggio inesistente: {char_id}")
        required = ("voice", "tone", "pace", "seed", "enabled", "parameters")
        missing = [name for name in required if name not in voice]
        if missing:
            raise StorycastError(f"Campi voce mancanti per '{char_id}': {', '.join(missing)}")
        instructions = voice.get("instructions")
        if instructions is None:
            instruction = voice.get("instruction")
        else:
            instruction = instructions.get(selected) if isinstance(instructions, dict) else None
        if not isinstance(instruction, str) or not instruction.strip():
            raise StorycastError(f"Istruzione mancante per '{char_id}' nel profilo '{selected}'")
        if not isinstance(voice["seed"], int) or not isinstance(voice["enabled"], bool):
            raise StorycastError(f"seed/enabled non validi per '{char_id}'")
        if voice["enabled"]:
            resolved = dict(voice)
            resolved.update(instruction=instruction.strip(), language=profile["spoken_language"],
                            spoken_language=profile["spoken_language"],
                            instruction_language=profile["instruction_language"],
                            instruction_profile=selected, emotion_template=profile["emotion_template"],
                            unknown_emotion_value=profile.get("unknown_emotion_value", "neutral"),
                            emotion_mapping=data.get("emotion_mappings", {}).get(selected, {}),
                            legacy_hash_compat=legacy_hash_compat)
            result[char_id] = resolved
    for char_id in characters:
        if char_id not in result:
            raise StorycastError(f"Voce abilitata mancante per il personaggio '{char_id}'")
    return result


def normalize_spoken_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\ufeff", "").replace("\u00a0", " ")).strip()


def _word_cut(words: list[str], limit: int) -> int:
    weak = {"a", "al", "da", "di", "in", "con", "su", "per", "tra", "fra", "e", "o", "ma", "che", "the", "a", "an", "and", "or", "but"}
    cut = min(limit, len(words))
    while cut > max(1, limit - 8) and words[cut - 1].lower().strip("\"'(),") in weak:
        cut -= 1
    return cut


def split_text(text: str, max_words: int, hard_limit: int) -> list[str]:
    if max_words <= 0 or hard_limit < max_words:
        raise StorycastError("Limiti di suddivisione TTS non validi")
    text = normalize_spoken_text(text)
    if not text:
        raise StorycastError("Testo TTS vuoto")
    sentences = [p.strip() for p in re.split(r"(?<=[.!?;])(?:[\"']?)(?=\s+)", text) if p.strip()]
    pieces: list[str] = []
    for sentence in sentences:
        pending = [sentence]
        while pending:
            part = pending.pop(0)
            words = part.split()
            if len(words) <= hard_limit:
                pieces.append(part)
                continue
            comma = re.search(r",\s+", part)
            valid_commas = [m for m in re.finditer(r",\s+", part) if len(part[:m.end()].split()) <= hard_limit]
            if comma and valid_commas:
                split_at = valid_commas[-1].end()
                pending[:0] = [part[:split_at].strip(), part[split_at:].strip()]
            else:
                cut = _word_cut(words, hard_limit)
                pending[:0] = [" ".join(words[:cut]), " ".join(words[cut:])]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip()
        if current and len(candidate.split()) > max_words:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def metadata_path(root: Path, index: int, speaker: str) -> Path:
    return root / "work/metadata/audio_segments" / f"{index:04d}_{speaker}.json"


def wav_path(root: Path, index: int, speaker: str) -> Path:
    return root / "work/audio_segments" / f"{index:04d}_{speaker}.wav"


def wav_info(path: Path) -> dict[str, Any]:
    try:
        with wave.open(str(path), "rb") as wav:
            channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
            if channels != 1 or width != 2 or rate <= 0 or frames <= 0 or wav.getcomptype() != "NONE":
                raise StorycastError(f"WAV non valido o non PCM mono 16-bit: {path}")
            data = wav.readframes(frames)
    except (wave.Error, EOFError, OSError) as exc:
        raise StorycastError(f"WAV corrotto o illeggibile: {path}") from exc
    samples = struct.unpack(f"<{frames}h", data)
    silent = sum(abs(value) < 32 for value in samples)
    squares = sum(value * value for value in samples)
    peak = max(abs(value) for value in samples)
    clipped = sum(abs(value) >= 32760 for value in samples)
    return {"duration": frames / rate, "sample_rate": rate, "channels": channels, "sample_width": width,
            "frames": frames, "silence_percent": silent * 100.0 / frames,
            "rms": math.sqrt(squares / frames) / 32768.0, "peak": peak / 32768.0,
            "clipped_percent": clipped * 100.0 / frames, "complete_silence": peak == 0,
            "wav_hash": file_hash(path)}


def effective_instruction(voice: dict[str, Any], entry: dict[str, Any]) -> str:
    return instruction_details(voice, entry)["effective_instruction"]


def instruction_details(voice: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    original = entry.get("emotion")
    mapped = None
    if original:
        mapping = voice.get("emotion_mapping", {})
        mapped = mapping.get(str(original).strip().lower(), voice.get("unknown_emotion_value", "neutral"))
    extra = voice.get("emotion_template", " Emozione richiesta: {emotion}.").format(emotion=mapped) if mapped else ""
    return {"emotion_original": original, "emotion_instruction_value": mapped,
            "instruction_profile": voice.get("instruction_profile", "italian_legacy"),
            "instruction_language": voice.get("instruction_language", "Italian"),
            "spoken_language": voice.get("spoken_language", voice.get("language", "Italian")),
            "effective_instruction": voice["instruction"].strip() + extra}


def generation_hashes(*, voice: str, language: str, instruction: str,
                      parameters: dict[str, Any], effective_seed: int,
                      default_seed: int, alternate_seed: bool, model_hash: str,
                      backend: str, text_hash: str, instruction_profile: str | None = None,
                      instruction_language: str | None = None,
                      spoken_language: str | None = None) -> dict[str, Any]:
    """Ricostruisce deterministicamente identità vocale e generazione effettiva."""
    voice_payload = {
        "voice": voice, "language": language, "instruction": instruction,
        "parameters": parameters,
    }
    if instruction_profile is not None:
        voice_payload.update(instruction_profile=instruction_profile,
                             instruction_language=instruction_language,
                             spoken_language=spoken_language or language)
    voice_hash = canonical_hash(voice_payload)
    seed_origin = {
        "default_seed": default_seed, "effective_seed": effective_seed,
        "mode": "alternate" if alternate_seed else "default",
    }
    generation_payload = {
        "voice_config_hash": voice_hash, "seed_origin": seed_origin,
        "model_hash": model_hash, "backend": backend,
    }
    generation_hash = canonical_hash(generation_payload)
    effective_hash = canonical_hash({
        "generation_config_hash": generation_hash, "text_hash": text_hash,
    })
    return {
        "voice_config_hash": voice_hash,
        "generation_config_hash": generation_hash,
        "effective_generation_hash": effective_hash,
        "seed_origin": seed_origin,
    }


def build_plan(root: Path, entries: list[dict], voices: dict[str, dict], config: dict) -> list[dict[str, Any]]:
    model_identity = {"id": config["model"]["id"], "revision": config["model"]["revision"], "backend": config["backend"]}
    result = []
    for entry in entries:
        voice = voices[entry["speaker"]]
        spoken = normalize_spoken_text(entry["text"])
        chunks = split_text(spoken, int(config["text"]["max_words"]), int(config["text"]["hard_limit_words"]))
        details = instruction_details(voice, entry)
        instruction = details["effective_instruction"]
        text_hash = canonical_hash(spoken)
        hashes = generation_hashes(
            voice=voice["voice"], language=voice["language"], instruction=instruction,
            parameters=voice["parameters"], effective_seed=voice["seed"],
            default_seed=voice["seed"], alternate_seed=False,
            model_hash=canonical_hash(model_identity), backend=config["backend"],
            text_hash=text_hash,
            instruction_profile=None if voice.get("legacy_hash_compat") else details["instruction_profile"],
            instruction_language=None if voice.get("legacy_hash_compat") else details["instruction_language"],
            spoken_language=None if voice.get("legacy_hash_compat") else details["spoken_language"],
        )
        item = {
            "index": entry["index"], "speaker": entry["speaker"], "text": entry["text"],
            "emotion": entry.get("emotion"), "voice": voice["voice"], "language": voice["language"],
            "emotion_original": details["emotion_original"],
            "emotion_instruction_value": details["emotion_instruction_value"],
            "instruction_profile": details["instruction_profile"],
            "instruction_language": details["instruction_language"],
            "spoken_language": details["spoken_language"],
            "legacy_hash_compat": voice.get("legacy_hash_compat", False),
            "seed": voice["seed"], "instruction": instruction, "parameters": voice["parameters"],
            "chunks": chunks, "text_hash": text_hash, "voice_config_hash": hashes["voice_config_hash"],
            "generation_config_hash": hashes["generation_config_hash"],
            "effective_generation_hash": hashes["effective_generation_hash"],
            "seed_origin": hashes["seed_origin"],
            "model_hash": canonical_hash(model_identity), "model": config["model"]["id"],
            "wav_path": wav_path(root, entry["index"], entry["speaker"]),
            "metadata_path": metadata_path(root, entry["index"], entry["speaker"]),
            "subsegment_pause": float(config["audio"]["subsegment_pause_seconds"]),
            "pause_after": entry.get("pause"), "cache_status": "missing",
        }
        item["cache_status"] = cache_status(item)
        result.append(item)
    return result


def cache_status(item: dict[str, Any]) -> str:
    wav, meta = item["wav_path"], item["metadata_path"]
    if not wav.exists() and not meta.exists():
        return "missing"
    if not wav.is_file() or not meta.is_file():
        return "regenerate"
    try:
        saved = load_json(meta)
        info = wav_info(wav)
    except StorycastError:
        return "regenerate"
    if saved.get("text_hash") != item["text_hash"] or saved.get("model_hash") != item["model_hash"]:
        return "regenerate"
    try:
        effective_seed = saved.get("seed")
        alternate = saved.get("alternate_seed") is True
        if not isinstance(effective_seed, int) or isinstance(effective_seed, bool):
            return "regenerate"
        actual = generation_hashes(
            voice=saved.get("backend_voice", saved.get("voice")),
            language=saved.get("language"),
            instruction=saved.get("effective_instruction", saved.get("tts_instruction")),
            parameters=saved.get("parameters"), effective_seed=effective_seed,
            default_seed=item["seed"], alternate_seed=alternate,
            model_hash=saved.get("model_hash"), backend=saved.get("backend"),
            text_hash=saved.get("text_hash"),
            instruction_profile=saved.get("instruction_profile"),
            instruction_language=saved.get("instruction_language"),
            spoken_language=saved.get("spoken_language"),
        )
    except (TypeError, ValueError):
        return "regenerate"
    if saved.get("voice_config_hash") != actual["voice_config_hash"]:
        return "regenerate"
    if saved.get("generation_config_hash") != actual["generation_config_hash"]:
        return "regenerate"
    if saved.get("effective_generation_hash") != actual["effective_generation_hash"]:
        return "regenerate"
    # La coerenza interna dei metadata non basta: la cache deve anche
    # rappresentare esattamente il piano richiesto in questa esecuzione.
    if saved.get("voice_config_hash") != item.get("voice_config_hash"):
        return "regenerate"
    # Un seed alternativo esplicitamente registrato e internamente coerente è
    # il piano effettivo di quel WAV: non va confrontato col seed canonico.
    if not alternate:
        if saved.get("generation_config_hash") != item.get("generation_config_hash"):
            return "regenerate"
        if saved.get("effective_generation_hash") != item.get("effective_generation_hash"):
            return "regenerate"
    if (saved.get("wav_hash") != info["wav_hash"] or saved.get("status") != "valid"
            or saved.get("partial") is True or saved.get("generation_completed") is not True
            or saved.get("audio_qc_schema_version") != QC_SCHEMA_VERSION
            or saved.get("qc_state") != "valid"):
        return "regenerate"
    return "valid"


def create_mock_pcm(item: dict[str, Any], config: dict) -> tuple[bytes, int, list[dict[str, Any]]]:
    rate = int(config["audio"]["sample_rate"])
    seconds_per_word = float(config["audio"]["mock_seconds_per_word"])
    base = 220 + (int(hashlib.sha256(item["voice"].encode()).hexdigest()[:4], 16) % 280)
    frames = bytearray()
    parts = []
    for number, chunk in enumerate(item["chunks"], 1):
        duration = max(0.18, len(chunk.split()) * seconds_per_word)
        count = round(duration * rate)
        for i in range(count):
            sample = int(2600 * math.sin(2 * math.pi * base * i / rate))
            frames.extend(struct.pack("<h", sample))
        parts.append({"index": number, "text": chunk, "duration": count / rate})
        if number < len(item["chunks"]):
            pause_frames = round(item["subsegment_pause"] * rate)
            frames.extend(b"\0\0" * pause_frames)
            parts[-1]["pause_after"] = pause_frames / rate
        else:
            parts[-1]["pause_after"] = 0.0
    return bytes(frames), rate, parts


def _real_generator(config: dict):
    model_dir = Path(config["model"]["local_dir"])
    if not model_dir.is_dir() or not (model_dir / "config.json").is_file():
        raise StorycastError(f"Modello assente: {model_dir}. Consultare docs/MODELLI.md; nessun download automatico viene eseguito.")
    try:
        import numpy as np
        import torch
        from qwen_tts import Qwen3TTSModel
    except ImportError as exc:
        raise StorycastError("Runtime Qwen3-TTS assente nell'immagine storycast-tts; costruire Dockerfile.tts.") from exc
    captured: list[str] = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = Qwen3TTSModel.from_pretrained(str(model_dir), device_map="cpu", dtype=torch.float32)
    captured.extend(str(item.message) for item in caught)

    def generate(item: dict[str, Any]) -> tuple[bytes, int, list[dict[str, Any]]]:
        all_audio = []
        parts = []
        for number, chunk in enumerate(item["chunks"], 1):
            random.seed(item["seed"]); np.random.seed(item["seed"]); torch.manual_seed(item["seed"])
            kwargs = dict(item["parameters"])
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                wavs, rate = model.generate_custom_voice(text=chunk, language=item["spoken_language"], speaker=item["voice"], instruct=item["instruction"], **kwargs)
            captured.extend(str(warning.message) for warning in caught)
            audio = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
            pcm = np.clip(audio, -1.0, 1.0)
            pcm = (pcm * 32767).astype("<i2")
            all_audio.append(pcm)
            duration = len(pcm) / int(rate)
            parts.append({"index": number, "text": chunk, "duration": duration, "pause_after": 0.0})
            if number < len(item["chunks"]):
                silence = np.zeros(round(item["subsegment_pause"] * int(rate)), dtype="<i2")
                all_audio.append(silence); parts[-1]["pause_after"] = len(silence) / int(rate)
        return np.concatenate(all_audio).tobytes(), int(rate), parts
    return generate, captured


def _write_wav_partial(path: Path, pcm: bytes, rate: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".partial", dir=path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with wave.open(str(tmp), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(pcm)
        with tmp.open("rb") as handle: os.fsync(handle.fileno())
        tmp.chmod(0o644)
        return tmp
    except Exception:
        raise


def _write_wav_atomic(path: Path, pcm: bytes, rate: int) -> None:
    temporary = _write_wav_partial(path, pcm, rate)
    try: os.replace(temporary, path)
    finally: temporary.unlink(missing_ok=True)


def generate(root: Path, plan: list[dict], config: dict, backend: str, dry_run: bool = False,
             start: int | None = None, end: int | None = None, force_index: int | None = None,
             state_path: Path | None = None, sleeper=time.sleep) -> dict[str, int]:
    selected = [item for item in plan if (start is None or item["index"] >= start) and (end is None or item["index"] <= end)]
    if force_index is not None:
        selected = [item for item in plan if item["index"] == force_index]
        if not selected:
            raise StorycastError(f"Indice battuta inesistente: {force_index}")
    summary = {"selected": len(selected), "generated": 0, "cached": 0, "would_generate": 0}
    if dry_run:
        summary["would_generate"] = sum(item["cache_status"] != "valid" or item["index"] == force_index for item in selected)
        return summary
    needed = [item for item in selected if item["cache_status"] != "valid" or item["index"] == force_index]
    summary["cached"] = len(selected) - len(needed)
    if not needed:
        return summary
    load_started = time.monotonic()
    if backend == "mock":
        generator, runtime_warnings = (lambda item: create_mock_pcm(item, config)), []
    else:
        generator, runtime_warnings = _real_generator(config)
    load_seconds = time.monotonic() - load_started
    cooldown = CpuCooldown(config, state_path=state_path, sleeper=sleeper)
    for item in selected:
        if item["cache_status"] == "valid" and item["index"] != force_index:
            continue
        try:
            inference_started = time.monotonic()
            pcm, rate, subsegments = generator(item)
            inference_seconds = time.monotonic() - inference_started
            partial = _write_wav_partial(item["wav_path"], pcm, rate)
            info = wav_info(partial)
            metadata = {
                "schema_version": 2, "index": item["index"], "speaker": item["speaker"], "character": item["speaker"],
                "original_text": item["text"], "emotion": item["emotion"],
                "emotion_original": item["emotion_original"], "emotion_instruction_value": item["emotion_instruction_value"],
                "tts_instruction": item["instruction"], "instruction_profile": item["instruction_profile"],
                "instruction_language": item["instruction_language"], "spoken_language": item["spoken_language"],
                "voice": item["voice"], "requested_voice": item["voice"], "backend_voice": item["voice"], "language": item["spoken_language"], "seed": item["seed"],
                "model": item["model"], "backend": backend, "parameters": item["parameters"], "effective_instruction": item["instruction"], "config_hash": item["voice_config_hash"],
                "subsegments": subsegments, "subsegment_pause_seconds": item["subsegment_pause"],
                "explicit_pause_after_seconds": item["pause_after"], "duration": info["duration"],
                "sample_rate": info["sample_rate"], "channels": info["channels"], "sample_width": info["sample_width"],
                "text_hash": item["text_hash"], "voice_config_hash": item["voice_config_hash"], "model_hash": item["model_hash"],
                "generation_config_hash": item["generation_config_hash"],
                "effective_generation_hash": item["effective_generation_hash"], "seed_origin": item["seed_origin"],
                "wav_hash": info["wav_hash"], "generated_at": datetime.now(timezone.utc).isoformat(), "status": "valid", "error": None,
                "generation_completed": True, "partial": False, "attempt_number": 2 if item.get("alternate_seed") else 1,
                "alternate_seed": item.get("alternate_seed", False),
                "model_load_seconds": load_seconds, "inference_seconds": inference_seconds,
                "inference_to_audio_ratio": inference_seconds / info["duration"],
                "rms": info["rms"], "peak": info["peak"], "silence_percent": info["silence_percent"],
                "clipped_percent": info["clipped_percent"], "complete_silence": info["complete_silence"],
                "warnings": list(dict.fromkeys(runtime_warnings)),
            }
            check = ({"qc_state":"valid", "reasons":[], "audio":info}
                     if backend == "mock" else
                     technical_qc(partial, item["text"], config, metadata=metadata, voice=item["voice"], allow_partial_container=True))
            metadata.update(audio_qc_schema_version=QC_SCHEMA_VERSION, qc_state=check["qc_state"],
                            qc_reasons=check["reasons"], review_state="pending")
            failed = check["qc_state"] != "valid"
            if failed:
                metadata.update(status="rejected", partial=True)
                rejected = item["wav_path"].parents[1]/"diagnostics/rejected_segments"
                rejected.mkdir(parents=True, exist_ok=True)
                os.replace(partial, rejected/f"{item['wav_path'].stem}_attempt1_{int(time.time())}.wav")
                write_json(item["metadata_path"], metadata)
                if backend == "real":
                    cooldown.wait_after_inference(segment_index=item["index"], attempt_number=1, failed=True, next_action="retry_tts")
                    if int(config["cpu_cooldown"]["retry_limit"]) > 0 and not item.get("alternate_seed"):
                        cooldown.wait_after_error(segment_index=item["index"], attempt_number=1)
                        retry=dict(item); default=item["seed"]
                        retry["seed"]=default+int(config["verification"].get("alternate_seed_offset",100003))
                        retry["alternate_seed"]=True
                        retry.update(generation_hashes(voice=retry["voice"],language=retry["language"],instruction=retry["instruction"],
                            parameters=retry["parameters"],effective_seed=retry["seed"],default_seed=default,alternate_seed=True,
                            model_hash=retry["model_hash"],backend=backend,text_hash=retry["text_hash"],
                            instruction_profile=None if retry.get("legacy_hash_compat") else retry.get("instruction_profile"),
                            instruction_language=None if retry.get("legacy_hash_compat") else retry.get("instruction_language"),
                            spoken_language=None if retry.get("legacy_hash_compat") else retry.get("spoken_language")))
                        retry_config=json.loads(json.dumps(config)); retry_config["cpu_cooldown"]["retry_limit"]=0
                        retried=generate(root,[retry],retry_config,backend,force_index=item["index"],state_path=state_path,sleeper=sleeper)
                        summary["generated"] += retried["generated"]
                        return summary
                raise StorycastError(f"Segmento {item['index']} non valido ({check['qc_state']}); retry esplicito con alternate seed richiesto")
            metadata["status"] = "valid"
            # Metadata e WAV sono promossi soltanto dopo un QC completo.
            write_json(item["metadata_path"].with_suffix(".json.partial"), metadata)
            os.replace(partial, item["wav_path"])
            os.replace(item["metadata_path"].with_suffix(".json.partial"), item["metadata_path"])
            summary["generated"] += 1
            if backend == "real": cooldown.wait_after_inference(segment_index=item["index"], attempt_number=1)
        except Exception as exc:
            if not isinstance(exc, StorycastError) or "retry esplicito" not in str(exc):
                write_json(item["metadata_path"], {"schema_version": 4, "index": item["index"], "speaker": item["speaker"], "status": "error", "partial": True, "generation_completed": False, "qc_state": "partial", "error": str(exc), "generated_at": datetime.now(timezone.utc).isoformat()})
                if backend == "real":
                    cooldown.wait_after_inference(segment_index=item["index"], attempt_number=1, failed=True, next_action="retry_tts")
                    if int(config["cpu_cooldown"]["retry_limit"]) > 0 and not item.get("alternate_seed"):
                        cooldown.wait_after_error(segment_index=item["index"], attempt_number=1)
            raise
    return summary


def verify_plan(plan: list[dict], config: dict) -> list[dict[str, Any]]:
    result = []
    limits = config["verification"]
    for item in plan:
        status = cache_status(item)
        errors = []
        info = None
        if status == "valid":
            info = wav_info(item["wav_path"])
            word_count = max(1, len(normalize_spoken_text(item["text"]).split()))
            ratio = info["duration"] / word_count
            if info["duration"] < limits["min_duration_seconds"]: errors.append("durata_troppo_breve")
            if not limits["min_seconds_per_word"] <= ratio <= limits["max_seconds_per_word"]: errors.append("durata_per_parola_anomala")
            if info["silence_percent"] > limits["max_silence_percent"]: errors.append("silenzio_eccessivo")
        result.append({"index": item["index"], "speaker": item["speaker"], "status": "invalid" if errors else status, "errors": errors, "audio": info})
    return result


def merge_audio(root: Path, plan: list[dict], config: dict, backup_existing: bool = True,
                output_rel: str = "output/storycast_audio.wav",
                manifest_rel: str = "work/metadata/audio_manifest.json",
                timeline_rel: str = "work/timeline/timeline.json",
                input_path: Path | None = None) -> dict[str, Any]:
    checks = verify_plan(plan, config)
    invalid = [x for x in checks if x["status"] != "valid"]
    if invalid:
        raise StorycastError("Audio incompleto o non valido; eseguire tts-verify prima del merge")
    output = root / output_rel
    backup = None
    if output.exists():
        wav_info(output)
        if not backup_existing:
            raise StorycastError("Output valido già presente; richiedere backup esplicito")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = output.parent / "backups" / f"{output.stem}_{stamp}.wav"
        backup.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(output, backup)
    rate = int(config["audio"]["sample_rate"])
    pcm = bytearray(); elements = []; cursor = 0.0
    timeline_data = []
    for position, (item, check) in enumerate(zip(plan, checks)):
        with wave.open(str(item["wav_path"]), "rb") as wav:
            if wav.getframerate() != rate or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise StorycastError(f"Formato WAV incompatibile: {item['wav_path']}")
            data = wav.readframes(wav.getnframes())
        duration = check["audio"]["duration"]; start = cursor; end = start + duration
        pcm.extend(data)
        elements.append({"type": "segment", "index": item["index"], "speaker": item["speaker"], "audio_file": item["wav_path"].relative_to(root).as_posix(), "start": start, "end": end, "duration": duration})
        timeline_data.append((item["index"], start, end, duration))
        cursor = end
        if position < len(plan) - 1:
            pause = item["pause_after"] if item["pause_after"] is not None else float(config["audio"]["utterance_pause_seconds"])
            pause_frames = round(float(pause) * rate); actual = pause_frames / rate
            if pause_frames:
                pcm.extend(b"\0\0" * pause_frames)
                elements.append({"type": "pause", "after_index": item["index"], "source": "explicit" if item["pause_after"] is not None else "default", "start": cursor, "end": cursor + actual, "duration": actual})
                cursor += actual
    _write_wav_atomic(output, bytes(pcm), rate)
    manifest = {"schema_version": 1, "output": output.relative_to(root).as_posix(), "sample_rate": rate, "elements": elements, "total_duration": cursor, "wav_hash": file_hash(output), "created_at": datetime.now(timezone.utc).isoformat(), "backup": backup.relative_to(root).as_posix() if backup else None}
    write_json(root / manifest_rel, manifest)
    update_timeline(root, timeline_data, plan, config, root / timeline_rel, input_path or root / "input/dialogo.txt")
    return manifest


def update_timeline(root: Path, timing: list[tuple[int, float, float, float]], plan: list[dict], config: dict, path: Path, input_path: Path) -> None:
    characters = load_characters(root); entries = parse_dialogue(input_path, characters)
    timeline = make_timeline(entries, characters, root)
    by_index = {index: (start, end, duration) for index, start, end, duration in timing}
    plan_by_index = {item["index"]: item for item in plan}
    for item in timeline:
        if item["index"] in by_index:
            item["start"], item["end"], item["duration"] = by_index[item["index"]]
            item["audio_status"] = "valid"; item["status"] = "audio_ready"
            planned = plan_by_index[item["index"]]
            item["audio_file"] = planned["wav_path"].relative_to(root).as_posix()
            item["voice"] = planned["voice"]
            item["wav_hash"] = wav_info(planned["wav_path"])["wav_hash"]
            is_last = item["index"] == max(plan_by_index)
            item["pause_after"] = 0.0 if is_last else (
                planned["pause_after"] if planned["pause_after"] is not None
                else float(config["audio"]["utterance_pause_seconds"])
            )
    write_json(path, {"schema_version": 1, "entries": timeline})

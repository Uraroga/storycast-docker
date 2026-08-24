from __future__ import annotations

import datetime as dt
import json
import math
import os
import struct
import time
import wave
from pathlib import Path
from typing import Any, Callable

from .core import StorycastError, write_json_atomic

QC_SCHEMA_VERSION = 4
VALID_QC_STATES = {
    "valid", "suspicious_too_short", "suspicious_mostly_silent",
    "suspicious_hard_cut", "suspicious_incomplete_write", "partial",
    "rejected", "awaiting_human_review",
}


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def validate_cpu_cooldown(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("cpu_cooldown")
    if value is None:  # compatibilità dei fixture/mock; la configurazione reale la dichiara esplicitamente
        value = {"enabled": True, "profile": "conservative", "after_inference_seconds": 30,
                 "after_error_extra_seconds": 60, "max_parallel_inferences": 1, "retry_limit": 1}
        config["cpu_cooldown"] = value
    if not isinstance(value, dict):
        raise StorycastError("Sezione cpu_cooldown non valida in config/tts.json")
    if value.get("profile") == "conservative":
        if value.get("enabled") is not True:
            raise StorycastError("Il profilo conservative deve essere abilitato")
        if float(value.get("after_inference_seconds", 0)) < 30:
            raise StorycastError("after_inference_seconds non può essere inferiore a 30")
        if float(value.get("after_error_extra_seconds", 0)) < 60:
            raise StorycastError("after_error_extra_seconds non può essere inferiore a 60")
        if value.get("max_parallel_inferences") != 1:
            raise StorycastError("max_parallel_inferences deve essere 1")
        if not isinstance(value.get("retry_limit"), int) or not 0 <= value["retry_limit"] <= 1:
            raise StorycastError("retry_limit deve essere 0 o 1")
    return value


class CpuCooldown:
    """Attesa centralizzata, passiva e testabile tra inferenze TTS reali."""

    def __init__(self, config: dict[str, Any], *, state_path: Path | None = None,
                 sleeper: Callable[[float], None] = time.sleep,
                 clock: Callable[[], str] = utcnow,
                 logger: Callable[[str], None] = print):
        self.settings = validate_cpu_cooldown(config)
        self.state_path, self.sleeper, self.clock, self.logger = state_path, sleeper, clock, logger

    def _state(self, **changes: Any) -> None:
        if not self.state_path:
            return
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.is_file() else {"schema_version": 1}
        except (OSError, json.JSONDecodeError) as exc:
            raise StorycastError(f"state.json non aggiornabile durante cooldown: {exc}") from exc
        state.update(changes, updated_at=self.clock())
        write_json_atomic(self.state_path, state)

    def _wait(self, seconds: float, reason: str, *, segment_index: int, attempt_number: int,
              next_action: str, message: str) -> None:
        if not self.settings["enabled"] or seconds <= 0:
            return
        started = self.clock()
        self.logger(message)
        self._state(phase="cooldown", current_phase="cooldown", cooldown_reason=reason,
                    cooldown_started_at=started, cooldown_duration_seconds=seconds,
                    cooldown_remaining_seconds=seconds, next_action=next_action,
                    segment_index=segment_index, attempt_number=attempt_number)
        try:
            self.sleeper(seconds)
        except (KeyboardInterrupt, SystemExit):
            self._state(phase="interrupted", current_phase="cooldown_interrupted",
                        cooldown_interrupted_at=self.clock(), cooldown_remaining_seconds=None,
                        final_status="interrupted", next_action=next_action)
            raise
        ended = self.clock()
        self._state(last_cooldown={"reason": reason, "started_at": started, "ended_at": ended,
                                  "duration_seconds": seconds, "segment_index": segment_index,
                                  "attempt_number": attempt_number},
                    cooldown_remaining_seconds=0, cooldown_finished_at=ended,
                    current_phase=next_action)

    def wait_after_inference(self, *, segment_index: int, attempt_number: int,
                             failed: bool = False, next_action: str = "tts_running") -> None:
        self._wait(float(self.settings["after_inference_seconds"]),
                   "segmento_non_valido" if failed else "inferenza_completata",
                   segment_index=segment_index, attempt_number=attempt_number,
                   next_action=next_action,
                   message=f"[CPU] Inferenza completata. Pausa prudenziale: {self.settings['after_inference_seconds']} secondi.")

    def wait_after_error(self, *, segment_index: int, attempt_number: int,
                         next_action: str = "retry_tts") -> None:
        self._wait(float(self.settings["after_error_extra_seconds"]), "errore_prima_retry",
                   segment_index=segment_index, attempt_number=attempt_number,
                   next_action=next_action,
                   message=("[CPU] Segmento non valido. Pausa ordinaria completata.\n"
                            f"[CPU] Pausa aggiuntiva prima del tentativo correttivo: {self.settings['after_error_extra_seconds']} secondi."))


def _edge_silence(samples: tuple[int, ...], rate: int, threshold: int) -> tuple[float, float]:
    first = next((i for i, x in enumerate(samples) if abs(x) >= threshold), len(samples))
    last = next((i for i, x in enumerate(reversed(samples)) if abs(x) >= threshold), len(samples))
    return first / rate, last / rate


def analyze_wav(path: Path, silence_threshold: int = 164, *, allow_partial: bool = False) -> dict[str, Any]:
    if not allow_partial and (path.name.endswith(".partial") or ".partial." in path.name):
        raise StorycastError(f"File WAV parziale: {path}")
    try:
        with wave.open(str(path), "rb") as wav:
            channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
            compression = wav.getcomptype(); raw = wav.readframes(frames)
    except (wave.Error, EOFError, OSError) as exc:
        raise StorycastError(f"WAV corrotto o illeggibile: {path}") from exc
    expected = frames * channels * width
    if channels != 1 or width != 2 or rate <= 0 or frames <= 0 or compression != "NONE" or len(raw) != expected:
        raise StorycastError(f"WAV incompleto o formato non PCM mono 16-bit: {path}")
    samples = struct.unpack(f"<{frames}h", raw)
    active = sum(abs(x) >= silence_threshold for x in samples)
    squares = sum(x*x for x in samples); peak = max(abs(x) for x in samples)
    leading, trailing = _edge_silence(samples, rate, silence_threshold)
    window = max(1, min(frames, round(rate * .12)))
    tail_rms = math.sqrt(sum(x*x for x in samples[-window:]) / window) / 32768
    prior_start = max(0, frames - 3*window); prior_end = max(prior_start + 1, frames - window)
    prior = samples[prior_start:prior_end]
    prior_rms = math.sqrt(sum(x*x for x in prior) / len(prior)) / 32768 if prior else 0
    return {"duration": frames/rate, "sample_rate": rate, "channels": channels,
            "sample_width": width, "format": "PCM_S16LE", "frames": frames,
            "fully_written": len(raw) == expected, "active_duration": active/rate,
            "active_ratio": active/frames, "silence_percent": (frames-active)*100/frames,
            "leading_silence": leading, "trailing_silence": trailing,
            "rms": math.sqrt(squares/frames)/32768, "peak": peak/32768,
            "tail_rms": tail_rms, "pre_tail_rms": prior_rms,
            "tail_energy_ratio": tail_rms/max(prior_rms, 1e-9)}


def duration_interval(text: str, config: dict[str, Any], voice: str | None = None,
                      voice_samples: list[float] | None = None) -> dict[str, float]:
    words = max(1, len(text.split())); chars = len(text.strip())
    qc = config["verification"].get("technical_completeness", {})
    nominal = words / float(qc.get("words_per_second", 2.7)) + chars * float(qc.get("seconds_per_character", .012))
    nominal += sum(text.count(mark) for mark in ".?!;:") * float(qc.get("punctuation_seconds", .10))
    if voice_samples:
        empirical = sorted(voice_samples)[len(voice_samples)//2] * words
        nominal = (nominal + empirical) / 2
    return {"minimum": max(float(qc.get("absolute_min_seconds", .18)), nominal*float(qc.get("minimum_factor", .32))),
            "nominal": nominal, "maximum": max(nominal*float(qc.get("maximum_factor", 2.8)), 1.0)}


def technical_qc(path: Path, text: str, config: dict[str, Any], *, metadata: dict[str, Any] | None,
                 voice: str | None = None, voice_samples: list[float] | None = None,
                 allow_partial_container: bool = False) -> dict[str, Any]:
    qc = config["verification"].get("technical_completeness", {})
    try:
        info = analyze_wav(path, int(qc.get("silence_amplitude_threshold", 164)), allow_partial=allow_partial_container)
    except StorycastError as exc:
        state = "partial" if path.name.endswith(".partial") else "suspicious_incomplete_write"
        return {"audio_qc_schema_version": QC_SCHEMA_VERSION, "qc_state": state,
                "reasons": [str(exc)], "audio": None}
    expected = duration_interval(text, config, voice, voice_samples)
    reasons: list[str] = []; states: list[str] = []
    words = len(text.split())
    if info["duration"] < expected["minimum"] or (words >= 3 and info["active_duration"] < float(qc.get("min_active_seconds", .25))):
        states.append("suspicious_too_short"); reasons.append("durata_chiaramente_inferiore_all_intervallo_plausibile")
    if info["rms"] < float(qc.get("min_rms", .003)) or info["active_ratio"] < float(qc.get("min_active_ratio", .12)) or info["silence_percent"] > float(qc.get("max_silence_percent", 88)):
        states.append("suspicious_mostly_silent"); reasons.append("audio_attivo_o_energia_insufficienti")
    hard_cut = (info["duration"] < expected["nominal"]*float(qc.get("hard_cut_duration_factor", .72))
                and info["trailing_silence"] < float(qc.get("min_natural_tail_seconds", .035))
                and info["tail_rms"] > float(qc.get("hard_cut_tail_rms", .018)))
    if hard_cut:
        states.append("suspicious_hard_cut"); reasons.append("durata_breve_e_finale_energetico_senza_coda")
    required = {"index", "speaker", "original_text", "requested_voice", "backend_voice", "effective_instruction",
                "spoken_language", "seed", "parameters", "wav_hash", "status", "generation_completed"}
    if metadata is None or required-set(metadata):
        states.append("suspicious_incomplete_write"); reasons.append("metadata_incompleti")
    elif metadata.get("status") != "valid" or metadata.get("generation_completed") is not True or metadata.get("partial") is True:
        states.append("suspicious_incomplete_write"); reasons.append("backend_o_scrittura_non_completati")
    state = states[0] if states else "valid"
    return {"audio_qc_schema_version": QC_SCHEMA_VERSION, "qc_state": state,
            "review_state": "pending", "reasons": sorted(set(reasons)),
            "duration_interval": expected, "audio": info}

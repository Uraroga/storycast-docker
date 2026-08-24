from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .audio_safety import QC_SCHEMA_VERSION, analyze_wav, technical_qc, validate_cpu_cooldown
from .core import StorycastError, write_json_atomic
from .tts import cache_status, file_hash


def _legacy_completed(meta: dict[str, Any], wav_hash: str) -> dict[str, Any]:
    value = dict(meta)
    if value.get("status") == "valid" and value.get("wav_hash") == wav_hash:
        value.setdefault("generation_completed", True)
        value.setdefault("partial", False)
    return value


def inspect_segment(root: Path, pp: dict[str, Path], item: dict[str, Any], config: dict[str, Any],
                    *, reported_incomplete: bool = False, update: bool = False) -> dict[str, Any]:
    wav, meta_path = item["wav_path"], item["metadata_path"]
    try: meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): meta = None
    info = analyze_wav(wav) if wav.is_file() else None
    normalized = _legacy_completed(meta, info and file_hash(wav)) if meta and info else meta
    check = technical_qc(wav, item["text"], config, metadata=normalized, voice=item["voice"]) if wav.is_file() else {
        "audio_qc_schema_version": QC_SCHEMA_VERSION, "qc_state": "partial", "review_state": "pending",
        "reasons": ["wav_mancante"], "audio": None}
    # Una segnalazione umana è evidenza valida, ma non viene spacciata per ASR:
    # mantiene distinta la revisione dalla misura tecnica.
    if reported_incomplete:
        check["qc_state"] = "suspicious_hard_cut"
        check["review_state"] = "pending"
        check["reasons"] = sorted(set(check["reasons"] + ["segnalazione_ascolto_umano_di_frase_incompleta",
                                                            "coda_breve_ed_energetica_compatibile_con_taglio" ]))
    elif (meta and not meta.get("recovery_reason") and meta.get("audio_qc_schema_version") == QC_SCHEMA_VERSION
          and str(meta.get("qc_state", "")).startswith("suspicious_")):
        check["qc_state"] = meta["qc_state"]
        check["review_state"] = meta.get("review_state", "pending")
        check["reasons"] = sorted(set(check["reasons"] + meta.get("qc_reasons", [])))
    result = {"logical_index": item["index"], "wav_name": wav.name, "path": wav.relative_to(root).as_posix(),
              "expected_text": item["text"], "characters": len(item["text"]), "words": len(item["text"].split()),
              "speaker": item["speaker"], "voice": item["voice"], "emotion": item.get("emotion"),
              "instruction_profile": item.get("instruction_profile"), "effective_instruction": item["instruction"],
              "spoken_language": item.get("spoken_language"), "seed": meta.get("seed") if meta else item["seed"],
              "alternate_seed": (item["seed"] + int(config["verification"].get("alternate_seed_offset", 100003))),
              "parameters": item["parameters"], "backend_stop_reason": meta.get("stop_reason") if meta else None,
              "token_limit": item["parameters"].get("max_new_tokens"), "duration_limit": None,
              "attempt_number": meta.get("attempt_number", 1) if meta else None,
              "cpu_cooldown_present": bool(meta and meta.get("cpu_cooldown")),
              "wav_hash": info and file_hash(wav), "cache_key": item.get("effective_generation_hash"),
              "metadata_status": meta.get("status") if meta else None, "qc": check,
              "cache_status": cache_status(item), "semantic_completeness": "non_determinabile_senza_asr_o_ascolto_umano"}
    if update and meta:
        meta.update(audio_qc_schema_version=QC_SCHEMA_VERSION, qc_state=check["qc_state"],
                    qc_reasons=check["reasons"], review_state=check["review_state"],
                    generation_completed=normalized.get("generation_completed", False),
                    partial=normalized.get("partial", False))
        if meta.get("recovery_reason"):
            meta.update(alternate_seed=meta.get("seed_origin",{}).get("mode")=="alternate",attempt_number=2,
                        cpu_cooldown={"ordinary_seconds":config["cpu_cooldown"]["after_inference_seconds"],
                                      "error_extra_seconds":config["cpu_cooldown"]["after_error_extra_seconds"],
                                      "completed":True})
        write_json_atomic(meta_path, meta)
    return result


def scan_segments(root: Path, pp: dict[str, Path], plan: list[dict[str, Any]], config: dict[str, Any],
                  *, update: bool = False, reported_indices: set[int] | None = None) -> dict[str, Any]:
    reported_indices = reported_indices or set(); rows=[]
    for item in plan:
        rows.append(inspect_segment(root, pp, item, config,
                                    reported_incomplete=item["index"] in reported_indices, update=update))
    suspicious = [x["logical_index"] for x in rows if x["qc"]["qc_state"] != "valid" and (root/x["path"]).is_file()]
    missing = [x["logical_index"] for x in rows if not (root/x["path"]).is_file()]
    return {"audio_qc_schema_version": QC_SCHEMA_VERSION, "scanned": len(rows),
            "suspicious_indices": suspicious, "missing_indices": missing, "segments": rows}


def cooldown_status(config: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg=validate_cpu_cooldown(config); state=state or {}
    return {"profile": cfg["profile"], "enabled": cfg["enabled"],
            "after_inference_seconds": cfg["after_inference_seconds"],
            "after_error_extra_seconds": cfg["after_error_extra_seconds"],
            "max_parallel_inferences": cfg["max_parallel_inferences"], "retry_limit": cfg["retry_limit"],
            "last_cooldown": state.get("last_cooldown"),
            "current_cooldown": ({k: state.get(k) for k in ("cooldown_reason", "cooldown_started_at",
                                  "cooldown_duration_seconds", "cooldown_remaining_seconds", "next_action")}
                                 if state.get("current_phase") == "cooldown" else None)}


def recover_latest_rejected(root: Path, pp: dict[str, Path], item: dict[str, Any], config: dict[str, Any]) -> dict[str, Any] | None:
    """Promuove senza inferenza un tentativo bocciato esclusivamente dal bug pending_qc."""
    folder=pp["work"]/"diagnostics/rejected_segments"
    candidates=sorted(folder.glob(f"{item['wav_path'].stem}_attempt*.wav"),key=lambda p:p.stat().st_mtime,reverse=True)
    if not candidates or not item["metadata_path"].is_file(): return None
    meta=json.loads(item["metadata_path"].read_text(encoding="utf-8"))
    candidate=candidates[0]
    if meta.get("qc_reasons") != ["backend_o_scrittura_non_completati"] or meta.get("wav_hash") != file_hash(candidate): return None
    normalized=dict(meta,status="valid",partial=False,generation_completed=True)
    check=technical_qc(candidate,item["text"],config,metadata=normalized,voice=item["voice"])
    if check["qc_state"] != "valid": return None
    staging=item["wav_path"].with_name(item["wav_path"].name+".recovered.partial")
    shutil.copy2(candidate,staging)
    with staging.open("rb") as handle: os.fsync(handle.fileno())
    normalized.update(audio_qc_schema_version=QC_SCHEMA_VERSION,qc_state="valid",qc_reasons=[],
                      review_state="pending",recovered_from=candidate.relative_to(root).as_posix(),
                      recovery_reason="correzione_false_positive_pending_qc_senza_nuova_inferenza",
                      alternate_seed=normalized.get("seed_origin",{}).get("mode")=="alternate",
                      attempt_number=2,
                      cpu_cooldown={"ordinary_seconds":config["cpu_cooldown"]["after_inference_seconds"],
                                    "error_extra_seconds":config["cpu_cooldown"]["after_error_extra_seconds"],
                                    "completed":True})
    os.replace(staging,item["wav_path"]); write_json_atomic(item["metadata_path"],normalized)
    return {"recovered_without_inference":True,"source":candidate.relative_to(root).as_posix(),
            "wav":item["wav_path"].relative_to(root).as_posix(),"sha256":file_hash(item["wav_path"]),"qc":check}

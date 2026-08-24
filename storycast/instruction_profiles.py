from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .core import StorycastError, load_characters, write_json_atomic
from .tts import (active_instruction_profile, build_plan, cache_status, file_hash,
                  generate, load_instruction_configuration, load_tts_config,
                  load_voices, wav_info, set_instruction_profile)

ROOT = Path(os.environ.get("STORYCAST_ROOT", Path(__file__).resolve().parents[1])).resolve()
AB_ROOT = Path("work/instruction_ab_test")

AB_CASES = (
    ("vivian_instruction_it", "personaggio_1", "Benvenuti a Storycast. Oggi raccontiamo una storia davvero particolare.", "italian_legacy"),
    ("vivian_instruction_en", "personaggio_1", "Benvenuti a Storycast. Oggi raccontiamo una storia davvero particolare.", "english_default"),
    ("ryan_instruction_it", "personaggio_2", "Cominciamo con calma, seguendo ogni dettaglio della conversazione.", "italian_legacy"),
    ("ryan_instruction_en", "personaggio_2", "Cominciamo con calma, seguendo ogni dettaglio della conversazione.", "english_default"),
)


def instruction_status(root: Path) -> dict:
    data = load_instruction_configuration(root)
    active = active_instruction_profile(root, data)
    rows = {}
    for profile in sorted(data["instruction_profiles"]):
        voices = load_voices(root, instruction_profile=profile)
        rows[profile] = {
            **data["instruction_profiles"][profile],
            "voices": {speaker: {"voice": voice["voice"], "instruction": voice["instruction"]}
                       for speaker, voice in voices.items()},
            "active": profile == active,
        }
    return {"default_profile": data["instruction_profile"], "active_profile": active,
            "profiles": rows, "backend_fields": {"spoken_text": "text", "spoken_language": "language",
                                                   "voice_instruction": "instruct"}}


def ab_plan(root: Path) -> tuple[dict, list[dict]]:
    chars = load_characters(root); config = load_tts_config(root); result = []
    target = root / AB_ROOT
    for number, (name, speaker, text, profile) in enumerate(AB_CASES, 1):
        voices = load_voices(root, chars, instruction_profile=profile)
        entry = {"index": number, "speaker": speaker, "text": text, "emotion": None, "pause": None}
        item = build_plan(root, [entry], voices, config)[0]
        item["wav_path"] = target / f"{name}.wav"
        item["metadata_path"] = target / "metadata" / f"{name}.json"
        item["cache_status"] = cache_status(item)
        item["comparison_id"] = name
        result.append(item)
    return config, result


def ab_summary(root: Path) -> dict:
    config, plan = ab_plan(root)
    return {"backend": config["backend"], "model": config["model"]["id"], "same_seed": len({x["seed"] for x in plan}) == 1,
            "spoken_language": sorted({x["spoken_language"] for x in plan}),
            "cases": [{"id": x["comparison_id"], "speaker": x["speaker"], "voice": x["voice"],
                       "text": x["text"], "seed": x["seed"], "instruction_profile": x["instruction_profile"],
                       "instruction_language": x["instruction_language"], "effective_instruction": x["instruction"],
                       "wav": x["wav_path"].relative_to(root).as_posix(), "cache_status": x["cache_status"]} for x in plan]}


def ab_run(root: Path, dry_run: bool) -> dict:
    config, plan = ab_plan(root); summary = ab_summary(root)
    if dry_run:
        summary["dry_run"] = True; summary["inference_executed"] = False
        return summary
    generated = generate(root, plan, config, config["backend"])
    rows = []
    for item in plan:
        info = wav_info(item["wav_path"]); metadata = json.loads(item["metadata_path"].read_text(encoding="utf-8"))
        rows.append({"id": item["comparison_id"], "wav": item["wav_path"].relative_to(root).as_posix(),
                     "metadata": item["metadata_path"].relative_to(root).as_posix(), "duration": info["duration"],
                     "sha256": info["wav_hash"], "sample_rate": info["sample_rate"], "channels": info["channels"],
                     "complete_silence": info["complete_silence"], "voice": metadata["backend_voice"],
                     "text": metadata["original_text"], "seed": metadata["seed"],
                     "instruction_profile": metadata["instruction_profile"],
                     "instruction_language": metadata["instruction_language"],
                     "spoken_language": metadata["spoken_language"],
                     "effective_instruction": metadata["effective_instruction"],
                     "inference_seconds": metadata["inference_seconds"]})
    manifest = {"schema_version": 1, "purpose": "instruction_language_ab_only", "generated": generated,
                "constant_variables": ["text_per_voice", "voice", "seed", "model", "parameters", "spoken_language"],
                "only_variable": "instruction_profile_and_instruction_language", "cases": rows}
    write_json_atomic(root / AB_ROOT / "comparison_manifest.json", manifest)
    return manifest


def ab_status(root: Path) -> dict:
    summary = ab_summary(root); rows = []
    for item in ab_plan(root)[1]:
        if not item["wav_path"].is_file() or not item["metadata_path"].is_file():
            rows.append({"id": item["comparison_id"], "status": "missing"}); continue
        info = wav_info(item["wav_path"])
        rows.append({"id": item["comparison_id"], "status": cache_status(item), "duration": info["duration"],
                     "sha256": file_hash(item["wav_path"]), "complete_silence": info["complete_silence"]})
    return {"summary": summary, "cases": rows, "complete": len(rows) == 4 and all(x["status"] == "valid" for x in rows)}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="storycast-istruzioni-vocali")
    value.add_argument("command", choices=("tts-instruction-status", "tts-instruction-profile",
                                            "tts-instruction-ab-test", "tts-instruction-ab-test-status"))
    value.add_argument("profile", nargs="?"); value.add_argument("--dry-run", action="store_true")
    return value


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "tts-instruction-status": result = instruction_status(ROOT)
        elif args.command == "tts-instruction-profile":
            result = set_instruction_profile(ROOT, args.profile) if args.profile else instruction_status(ROOT)
        elif args.command == "tts-instruction-ab-test": result = ab_run(ROOT, args.dry_run)
        else: result = ab_status(ROOT)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (StorycastError, OSError, ValueError) as exc:
        print(f"ERRORE: {exc}", file=os.sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())

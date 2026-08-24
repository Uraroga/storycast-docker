from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .core import StorycastError, load_characters, parse_dialogue, write_json
from .tts import build_plan, cache_status, generate, load_tts_config, load_voices, merge_audio, verify_plan, wav_info

TEST_INPUT_REL = "input/test_reale_tts.txt"
TEST_AUDIO_DIR = "work/real_test/audio_segments"
TEST_META_DIR = "work/real_test/metadata/audio_segments"
TEST_OUTPUT_REL = "output/test_reale_storycast_audio.wav"
TEST_MANIFEST_REL = "work/metadata/test_reale_audio_manifest.json"
TEST_TIMELINE_REL = "work/timeline/test_reale_timeline.json"


def context(root: Path) -> tuple[dict, list[dict[str, Any]]]:
    characters = load_characters(root)
    voices = load_voices(root, characters)
    config = load_tts_config(root)
    if config["backend"] != "real":
        raise StorycastError(f"Il test reale richiede backend real, trovato: {config['backend']}")
    entries = parse_dialogue(root / TEST_INPUT_REL, characters)
    if len(entries) != 2 or {entry["speaker"] for entry in entries} != {"personaggio_1", "personaggio_2"}:
        raise StorycastError("Il dialogo di test reale deve contenere soltanto personaggio_1 e personaggio_2")
    plan = build_plan(root, entries, voices, config)
    for item in plan:
        basename = f"{item['index']:04d}_{item['speaker']}"
        item["wav_path"] = root / TEST_AUDIO_DIR / f"{basename}.wav"
        item["metadata_path"] = root / TEST_META_DIR / f"{basename}.json"
        item["cache_status"] = cache_status(item)
    return config, plan


def select(plan: list[dict], speaker: str | None) -> list[dict]:
    if speaker is None:
        return plan
    selected = [item for item in plan if item["speaker"] == speaker]
    if not selected:
        raise StorycastError(f"Speaker non presente nel test reale: {speaker}")
    return selected


def print_plan(config: dict, plan: list[dict], speaker: str | None = None) -> None:
    chosen = select(plan, speaker)
    print(f"Backend: {config['backend']}; modello: {config['model']['id']}")
    print(f"Percorso modello: {config['model']['local_dir']} (volume read-only)")
    for item in chosen:
        print(f"{item['index']:04d} {item['speaker']}: voice={item['voice']} language={item['language']} seed={item['seed']} cache={item['cache_status']}")
        print(f"  testo: {item['text']}")
        print(f"  instruct: {item['instruction']}")
        print(f"  output: {item['wav_path']}")


def run(root: Path, speaker: str | None, dry_run: bool) -> dict[str, int]:
    config, plan = context(root)
    chosen = select(plan, speaker)
    if speaker is None and not dry_run:
        raise StorycastError("Per l'inferenza reale specificare --speaker: le due voci devono essere eseguite una alla volta")
    if dry_run:
        print_plan(config, plan, speaker)
        return generate(root, chosen, config, "real", dry_run=True)
    return generate(root, chosen, config, "real")


def cache_invalidation_check(root: Path) -> dict[str, Any]:
    config, original_plan = context(root)
    if any(item["cache_status"] != "valid" for item in original_plan):
        raise StorycastError("Servono entrambi i WAV reali validi prima del test di invalidazione")
    source = root / "config/voices.yaml"
    copy_path = root / "work/real_test/config_variants/voices_modified_copy.yaml"
    copy_path.parent.mkdir(parents=True, exist_ok=True)
    source_before = source.read_text(encoding="utf-8")
    original = json.loads(source_before)
    modified = copy.deepcopy(original)
    modified["voices"]["personaggio_1"]["instruction"] += " Test temporaneo di invalidazione cache."
    copy_path.write_text(json.dumps(modified, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    characters = load_characters(root)
    changed_voices = load_voices(root, characters, copy_path)
    entries = parse_dialogue(root / TEST_INPUT_REL, characters)
    changed_plan = build_plan(root, entries, changed_voices, config)
    for item in changed_plan:
        basename = f"{item['index']:04d}_{item['speaker']}"
        item["wav_path"] = root / TEST_AUDIO_DIR / f"{basename}.wav"
        item["metadata_path"] = root / TEST_META_DIR / f"{basename}.json"
        item["cache_status"] = cache_status(item)
    result = {
        "normal_config_unchanged": source.read_text(encoding="utf-8") == source_before,
        "original": {item["speaker"]: item["cache_status"] for item in original_plan},
        "modified_copy": {item["speaker"]: item["cache_status"] for item in changed_plan},
        "expected": {"personaggio_1": "regenerate", "personaggio_2": "valid"},
    }
    # Ripristina anche la sola copia alla configurazione normale; la sorgente non viene mai scritta.
    copy_path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["copy_restored"] = json.loads(copy_path.read_text(encoding="utf-8")) == original
    if result["modified_copy"] != result["expected"] or not result["copy_restored"]:
        raise StorycastError(f"Invalidazione selettiva inattesa: {result}")
    write_json(root / "work/real_test/cache_invalidation_result.json", result)
    return result


def merge(root: Path) -> dict[str, Any]:
    config, plan = context(root)
    return merge_audio(root, plan, config, output_rel=TEST_OUTPUT_REL,
                       manifest_rel=TEST_MANIFEST_REL, timeline_rel=TEST_TIMELINE_REL,
                       input_path=root / TEST_INPUT_REL)


def status(root: Path) -> int:
    config, plan = context(root)
    print_plan(config, plan)
    checks = verify_plan(plan, config)
    for item in checks:
        suffix = ""
        if item["audio"]:
            suffix = f" duration={item['audio']['duration']:.3f}s rms={item['audio']['rms']:.6f} silence={item['audio']['silence_percent']:.2f}%"
        print(f"verify {item['index']:04d}: {item['status']}{suffix}")
    output = root / TEST_OUTPUT_REL
    if output.is_file():
        info = wav_info(output); print(f"merged: valid duration={info['duration']:.3f}s sha256={info['wav_hash']}")
    else:
        print("merged: missing")
    return 0 if all(item["status"] == "valid" for item in checks) else 1


def clean(root: Path, dry_run: bool, yes: bool) -> int:
    roots = [root / "work/real_test"]
    exact = [root / TEST_OUTPUT_REL, root / TEST_MANIFEST_REL, root / TEST_TIMELINE_REL]
    files = [path for folder in roots if folder.exists() for path in folder.rglob("*") if path.is_file()]
    files.extend(path for path in exact if path.is_file())
    files = sorted(set(files))
    for path in files: print(path.relative_to(root))
    if dry_run:
        print(f"Dry-run: {len(files)} file del solo test reale; nessuna eliminazione."); return 0
    if not yes:
        print("Pulizia annullata: usare --yes come conferma esplicita."); return 2
    for path in files: path.unlink()
    print(f"Eliminati {len(files)} file del solo test reale."); return 0

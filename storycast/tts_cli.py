from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .core import StorycastError, load_characters, parse_dialogue
from .tts import build_plan, generate, load_tts_config, load_voices, merge_audio, verify_plan, wav_info
from . import real_test
from . import episode, audio_qc

ROOT = Path(os.environ.get("STORYCAST_ROOT", Path(__file__).resolve().parents[1])).resolve()


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="storycast-tts", description="Pipeline TTS sequenziale Storycast")
    parser.add_argument("command", choices=("tts-status", "tts-check", "tts-plan", "tts-generate", "tts-regenerate", "tts-verify", "audio-merge", "audio-status", "tts-real-test", "tts-real-test-status", "tts-real-test-cache-check", "tts-real-test-merge", "tts-real-test-clean", "episode-01-plan", "episode-01-tts", "episode-01-audio", "episode-01-status", "episode-01-clean", "episode-01-audio-review", "episode-01-list-segments", "episode-01-segment-status", "episode-01-regenerate-segment", "episode-01-rebuild-after-segment", "episode-01-audio-qc", "episode-01-qc-status", "episode-01-migrate-metadata", "episode-01-approve-segment", "episode-01-reject-segment", "episode-01-review-status"))
    parser.add_argument("index", nargs="?", type=int)
    parser.add_argument("--input", type=Path, default=ROOT / "input/dialogo.txt")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from", dest="start", type=int)
    parser.add_argument("--to", dest="end", type=int)
    parser.add_argument("--mock", action="store_true", help="usa toni sintetici; non carica il modello")
    parser.add_argument("--speaker")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--alternate-seed", action="store_true")
    parser.add_argument("--prudent", action="store_true")
    return parser


def context(input_path: Path, mock: bool = False):
    characters = load_characters(ROOT)
    voices = load_voices(ROOT, characters)
    config = load_tts_config(ROOT)
    if mock:
        config = dict(config)
        config["backend"] = "mock"
    entries = parse_dialogue(input_path, characters)
    return characters, voices, config, entries, build_plan(ROOT, entries, voices, config)


def model_available(config: dict) -> bool:
    path = Path(config["model"]["local_dir"])
    return path.is_dir() and (path / "config.json").is_file()


def human_bytes(value: int) -> str:
    units = ["B", "KiB", "MiB", "GiB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]: return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def log_event(command: str, status: str, detail: str = "") -> None:
    path = ROOT / "logs/storycast-tts.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(timezone.utc).isoformat()} command={command} status={status} {detail}\n")
    except OSError as exc:
        print(f"AVVISO: log non scrivibile: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        if args.command.startswith("episode-01-") and args.command not in {"episode-01-plan","episode-01-tts","episode-01-audio","episode-01-status","episode-01-clean"}:
            config,_,plan=episode.context(ROOT)
            if args.command == "episode-01-list-segments":
                result=audio_qc.write_segment_lists(ROOT,plan,config)
                for item in result["segments"]: print(f"{item['index']:02d} {item['speaker']} ({item['expected_voice']}): {ROOT/item['wav_path']}")
                return 0
            if args.command == "episode-01-migrate-metadata":
                result=audio_qc.migrate_metadata(ROOT,plan,dry_run=args.dry_run)
                print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)); return 0
            if args.command == "episode-01-qc-status":
                result=audio_qc.qc(ROOT,plan,config,strict=False,update_states=False)
                print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)); return 0
            if args.command == "episode-01-audio-review":
                result=audio_qc.generate_review_page(ROOT,plan,config); print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
            if args.command == "episode-01-segment-status":
                if args.index is None: raise StorycastError("Indice battuta richiesto")
                print(json.dumps(audio_qc.segment_status(ROOT,plan,config,args.index),ensure_ascii=False,sort_keys=True,indent=2)); return 0
            if args.command == "episode-01-regenerate-segment":
                if args.index is None: raise StorycastError("Indice battuta richiesto")
                if not args.dry_run and not args.yes:
                    answer=input(f"Confermare la rigenerazione REALE del solo segmento {args.index}? [scrivere SI]: ")
                    if answer.strip() != "SI": raise StorycastError("Rigenerazione annullata: conferma esplicita non ricevuta")
                result=audio_qc.regenerate_segment(ROOT,plan,config,args.index,args.dry_run,args.alternate_seed,args.prudent,"real"); print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
            if args.command == "episode-01-rebuild-after-segment":
                if args.index is None: raise StorycastError("Indice battuta richiesto")
                print(json.dumps(episode.rebuild_after_segment(ROOT,args.index),ensure_ascii=False,sort_keys=True)); return 0
            if args.command == "episode-01-audio-qc":
                result=audio_qc.qc(ROOT,plan,config,args.strict); print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1 if result["status"]=="blocked" else 0
            if args.command in {"episode-01-approve-segment","episode-01-reject-segment"}:
                if args.index is None: raise StorycastError("Indice battuta richiesto")
                state="approved" if "approve" in args.command else "rejected"; print(json.dumps(audio_qc.set_state(ROOT,plan,args.index,state),ensure_ascii=False,sort_keys=True)); return 0
            if args.command == "episode-01-review-status":
                print(json.dumps(audio_qc.load_states(ROOT,plan),ensure_ascii=False,sort_keys=True,indent=2)); return 0
        if args.command == "episode-01-plan": episode.show_plan(ROOT); return 0
        if args.command == "episode-01-tts": print(json.dumps(episode.tts(ROOT,args.dry_run),ensure_ascii=False,sort_keys=True)); return 0
        if args.command == "episode-01-audio":
            result=episode.audio(ROOT); print(f"Audio episodio: {result['output']}, durata {result['total_duration']:.3f}s."); return 0
        if args.command == "episode-01-status": return episode.status(ROOT)
        if args.command == "episode-01-clean": return episode.clean(ROOT,args.dry_run,args.yes)
        if args.command == "tts-real-test":
            result = real_test.run(ROOT, args.speaker, args.dry_run)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            log_event(args.command, "ok", f"speaker={args.speaker} dry_run={args.dry_run} result={result}"); return 0
        if args.command == "tts-real-test-status":
            result = real_test.status(ROOT); log_event(args.command, "ok" if result == 0 else "incomplete"); return result
        if args.command == "tts-real-test-cache-check":
            result = real_test.cache_invalidation_check(ROOT)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True)); log_event(args.command, "ok"); return 0
        if args.command == "tts-real-test-merge":
            manifest = real_test.merge(ROOT)
            print(f"Audio test reale: {manifest['output']}, durata {manifest['total_duration']:.3f}s.")
            log_event(args.command, "ok", f"duration={manifest['total_duration']:.3f}"); return 0
        if args.command == "tts-real-test-clean":
            result = real_test.clean(ROOT, args.dry_run, args.yes)
            log_event(args.command, "ok" if result == 0 else "cancelled", f"dry_run={args.dry_run}"); return result
        _, voices, config, entries, plan = context(args.input, args.mock)
        backend = config["backend"]
        if args.command == "tts-status":
            checks = verify_plan(plan, config)
            counts = {name: sum(x["status"] == name for x in checks) for name in ("valid", "missing", "regenerate", "invalid")}
            print(f"Backend configurato: {config['backend']}; modello: {config['model']['id']}")
            print(f"Modello locale: {'disponibile' if model_available(config) else 'mancante'} in {config['model']['local_dir']}")
            voice_labels = ", ".join(f"{key}={value['voice']}" for key, value in voices.items())
            print(f"Voci abilitate: {voice_labels}")
            print(f"Audio: {counts}")
        elif args.command == "tts-check":
            if backend != "mock" and not model_available(config):
                raise StorycastError(f"Modello assente in {config['model']['local_dir']}. Consultare docs/MODELLI.md; nessun download automatico.")
            print(f"TTS check superato: {len(voices)} voci, {len(entries)} battute, backend {backend}.")
        elif args.command == "tts-plan":
            counts = {name: sum(x["cache_status"] == name for x in plan) for name in ("valid", "missing", "regenerate")}
            subsegments = sum(len(x["chunks"]) for x in plan)
            seconds = sum(max(.18, len(chunk.split()) * config["audio"]["mock_seconds_per_word"]) for item in plan for chunk in item["chunks"])
            size = round(seconds * config["audio"]["sample_rate"] * config["audio"]["sample_width"])
            print(f"Battute: {len(plan)}; sotto-segmenti previsti: {subsegments}")
            print(f"Voci: {', '.join(sorted({x['voice'] for x in plan}))}")
            print(f"Già validi: {counts['valid']}; mancanti: {counts['missing']}; da rigenerare: {counts['regenerate']}")
            print(f"Spazio WAV stimato (prudenziale mock): {human_bytes(size)}")
            print(f"Modello: {config['model']['id']} rev={config['model']['revision']} backend={backend} path={config['model']['local_dir']}")
        elif args.command == "tts-generate":
            result = generate(ROOT, plan, config, backend, args.dry_run, args.start, args.end)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        elif args.command == "tts-regenerate":
            if args.index is None: raise StorycastError("tts-regenerate richiede l'indice della battuta")
            result = generate(ROOT, plan, config, backend, False, force_index=args.index)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        elif args.command == "tts-verify":
            checks = verify_plan(plan, config)
            bad = [item for item in checks if item["status"] != "valid"]
            for item in checks: print(f"{item['index']:04d} {item['speaker']}: {item['status']} {','.join(item['errors'])}")
            if bad: raise StorycastError(f"Verifica fallita per {len(bad)} segmenti")
            print(f"Verifica superata: {len(checks)} WAV validi e cache coerente.")
        elif args.command == "audio-merge":
            manifest = merge_audio(ROOT, plan, config)
            print(f"Audio completo: {manifest['output']}, durata {manifest['total_duration']:.3f}s, elementi {len(manifest['elements'])}.")
            if manifest["backup"]: print(f"Backup precedente: {manifest['backup']}")
        elif args.command == "audio-status":
            output = ROOT / "output/storycast_audio.wav"; manifest = ROOT / "work/metadata/audio_manifest.json"
            if not output.is_file() or not manifest.is_file(): raise StorycastError("Audio completo o manifest mancante; eseguire audio-merge")
            info = wav_info(output); data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("wav_hash") != info["wav_hash"]: raise StorycastError("Hash audio completo diverso dal manifest")
            print(f"Audio valido: {output.relative_to(ROOT)}, {info['duration']:.3f}s, {info['sample_rate']} Hz, hash {info['wav_hash']}")
        log_event(args.command, "ok", f"backend={backend}")
        return 0
    except (StorycastError, OSError, ValueError) as exc:
        log_event(args.command, "error", str(exc).replace("\n", " "))
        print(f"ERRORE: {exc}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())

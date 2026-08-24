from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .core import StorycastError, load_characters, make_timeline, parse_dialogue, write_json

ROOT = Path(os.environ.get("STORYCAST_ROOT", Path(__file__).resolve().parents[1])).resolve()
DEFAULT_INPUT = ROOT / "input" / "dialogo.txt"
PARSED = ROOT / "work" / "dialogue" / "dialogue.json"
TIMELINE = ROOT / "work" / "timeline" / "timeline.json"
WORK_AREAS = ("dialogue", "audio_segments", "metadata", "timeline", "scenes")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="storycast", description="Controller offline Storycast")
    p.add_argument("command", choices=("status", "validate", "parse", "timeline", "check", "clean-work"))
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true", help="conferma esplicita per clean-work")
    return p


def validate(input_path: Path) -> tuple[dict, list]:
    characters = load_characters(ROOT)
    entries = parse_dialogue(input_path, characters)
    return characters, entries


def clean_work(dry_run: bool, yes: bool) -> int:
    from .work_manager import clean_work as clean_all, print_clean
    print_clean(clean_all(ROOT, dry_run=dry_run, yes=yes))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "status":
            chars = load_characters(ROOT)
            print(f"Storycast pronto: {len(chars)} personaggi configurati ({', '.join(chars)})")
            print(f"Input: {'presente' if args.input.is_file() else 'mancante'}; parsed: {'presente' if PARSED.is_file() else 'mancante'}; timeline: {'presente' if TIMELINE.is_file() else 'mancante'}")
        elif args.command == "validate":
            chars, entries = validate(args.input)
            print(f"Validazione superata: {len(chars)} personaggi, {len(entries)} battute.")
        elif args.command == "parse":
            _, entries = validate(args.input)
            write_json(PARSED, {"schema_version": 1, "source": args.input.relative_to(ROOT).as_posix(), "entries": entries})
            print(f"Parser completato: {PARSED.relative_to(ROOT)} ({len(entries)} battute).")
        elif args.command == "timeline":
            chars, entries = validate(args.input)
            data = make_timeline(entries, chars, ROOT)
            write_json(TIMELINE, {"schema_version": 1, "entries": data})
            print(f"Timeline creata: {TIMELINE.relative_to(ROOT)} ({len(data)} elementi).")
        elif args.command == "check":
            chars, entries = validate(args.input)
            timeline = make_timeline(entries, chars, ROOT)
            json.dumps(timeline, ensure_ascii=False)
            print("Check completo superato: configurazioni, master, UTF-8, dialogo e timeline validi.")
        elif args.command == "clean-work":
            return clean_work(args.dry_run, args.yes)
        return 0
    except (StorycastError, OSError, ValueError) as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

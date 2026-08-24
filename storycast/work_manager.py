from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

from .cleanup import _assert_no_active_generation
from .core import StorycastError


ROOT = Path(os.environ.get("STORYCAST_ROOT", Path(__file__).resolve().parents[1])).resolve()
PRESERVED_NAMES = {".gitkeep"}


def _human_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{value} B"


def _safe_work(root: Path) -> Path:
    work = (root / "work").resolve()
    if work == root.resolve() or work.parent != root.resolve():
        raise StorycastError("Percorso work non sicuro")
    work.mkdir(parents=True, exist_ok=True)
    return work


def _files_and_directories(work: Path) -> tuple[list[Path], list[Path]]:
    files: list[Path] = []
    directories: list[Path] = []
    for path in work.rglob("*"):
        if path.is_symlink() or path.is_file():
            files.append(path)
        elif path.is_dir():
            directories.append(path)
    return sorted(files), sorted(directories)


def clean_inventory(root: Path) -> dict[str, Any]:
    work = _safe_work(root)
    files, directories = _files_and_directories(work)
    deletable = [path for path in files if path.name not in PRESERVED_NAMES]
    preserved = [path for path in files if path.name in PRESERVED_NAMES]
    size = 0
    for path in deletable:
        try:
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode):
                size += info.st_size
        except FileNotFoundError:
            pass
    return {
        "work": work,
        "deletable_files": deletable,
        "directories": directories,
        "preserved": preserved,
        "bytes": size,
    }


def clean_work(root: Path, *, dry_run: bool, yes: bool) -> dict[str, Any]:
    if dry_run and yes:
        raise StorycastError("Usare --dry-run oppure --yes, non entrambi")
    if not dry_run and not yes:
        raise StorycastError("Nessuna cancellazione: usare prima --dry-run e poi --yes")
    _assert_no_active_generation(root)
    report = clean_inventory(root)
    deleted_files = deleted_directories = 0
    if yes:
        for path in report["deletable_files"]:
            try:
                path.unlink()
                deleted_files += 1
            except FileNotFoundError:
                continue
        for directory in sorted(report["directories"], key=lambda path: (len(path.parts), path.as_posix()), reverse=True):
            try:
                directory.rmdir()
                deleted_directories += 1
            except FileNotFoundError:
                continue
            except OSError:
                # Una directory con .gitkeep è intenzionalmente preservata.
                pass
    after = clean_inventory(root)
    result = {
        "dry_run": dry_run,
        "candidate_files": len(report["deletable_files"]),
        "candidate_directories": len(report["directories"]),
        "candidate_bytes": report["bytes"],
        "deleted_files": deleted_files,
        "deleted_directories": deleted_directories,
        "remaining": len(after["deletable_files"]),
        "preserved": [path.relative_to(root).as_posix() for path in after["preserved"]],
    }
    return result


def _file_kind(path: Path) -> str:
    parts = set(path.parts)
    if path.suffix.lower() == ".wav": return "wav_segmenti"
    if "metadata" in parts or path.name == "state.json": return "metadata_stato"
    if path.suffix.lower() == ".mp4" and "scenes" in parts: return "scene_video"
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}: return "immagini_derivate"
    if "visual" in parts or "derived" in parts: return "cache_visiva"
    if path.suffix.lower() in {".tmp", ".part", ".partial", ".lock"}: return "temporanei"
    return "altri"


def _state(path: Path) -> tuple[str, str]:
    if not path.is_file():
        return "sconosciuto", "state.json assente"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "non valido", "state.json non leggibile"
    status = str(data.get("final_status") or data.get("phase") or "sconosciuto")
    return status, "completa" if status in {"verified", "completed", "complete"} else "incompleta"


def work_status(root: Path, *, details: bool = False) -> dict[str, Any]:
    work = _safe_work(root)
    files, directories = _files_and_directories(work)
    regular_files = [path for path in files if path.name not in PRESERVED_NAMES]
    bytes_total = sum(path.lstat().st_size for path in regular_files if path.exists())
    episodes_root = work / "episodes"
    episodes = []
    if episodes_root.is_dir():
        for folder in sorted(path for path in episodes_root.iterdir() if path.is_dir() and not path.is_symlink()):
            episode_files = [path for path in folder.rglob("*") if path.is_file() or path.is_symlink()]
            kinds: dict[str, int] = {}
            for path in episode_files:
                kind = _file_kind(path.relative_to(work))
                kinds[kind] = kinds.get(kind, 0) + 1
            status, completion = _state(folder / "state.json")
            wav = sum(path.suffix.lower() == ".wav" for path in episode_files)
            metadata = sum(path.suffix.lower() == ".json" and "metadata" in path.parts for path in episode_files)
            episodes.append({
                "slug": folder.name,
                "files": len(episode_files),
                "bytes": sum(path.lstat().st_size for path in episode_files),
                "status": status,
                "completion": completion,
                "wav": wav,
                "metadata": metadata,
                "regeneration_data": wav > 0 and metadata > 0,
                "kinds": dict(sorted(kinds.items())),
            })
    incomplete = [item["slug"] for item in episodes if item["completion"] != "completa"]
    return {
        "files": len(regular_files),
        "directories": len(directories),
        "bytes": bytes_total,
        "episodes": episodes,
        "slugs": [item["slug"] for item in episodes],
        "incomplete": incomplete,
        "regeneration_episodes": [item["slug"] for item in episodes if item["regeneration_data"]],
        "details": details,
    }


def print_clean(result: dict[str, Any]) -> None:
    if result["dry_run"]:
        print("Dry-run pulizia work.")
        print(f"File da eliminare: {result['candidate_files']}")
        print(f"Directory candidate: {result['candidate_directories']}")
        print(f"Spazio eliminabile: {_human_size(result['candidate_bytes'])}")
        print("Nessuna eliminazione eseguita. Ripetere con --yes per confermare.")
        return
    print("Pulizia work completata.")
    print(f"File eliminati: {result['deleted_files']}")
    print(f"Directory eliminate: {result['deleted_directories']}")
    print(f"Spazio liberato: {_human_size(result['candidate_bytes'])}")
    print(f"Elementi rimasti in work: {result['remaining'] + len(result['preserved'])}")
    for path in result["preserved"]:
        print(f"Mantenuto: {path} — segnaposto .gitkeep")


def print_status(result: dict[str, Any]) -> None:
    print("Stato work")
    print(f"File: {result['files']}")
    print(f"Directory: {result['directories']}")
    print(f"Spazio occupato: {_human_size(result['bytes'])}")
    print(f"Episodi: {len(result['episodes'])} ({', '.join(result['slugs']) if result['slugs'] else 'nessuno'})")
    print(f"Lavorazioni incomplete: {', '.join(result['incomplete']) if result['incomplete'] else 'nessuna'}")
    print(f"Dati per rigenerazione segmenti: {', '.join(result['regeneration_episodes']) if result['regeneration_episodes'] else 'nessuno'}")
    if result["details"]:
        for item in result["episodes"]:
            kinds = ", ".join(f"{key}={value}" for key, value in item["kinds"].items()) or "vuoto"
            print(f"- {item['slug']}: file={item['files']}, spazio={_human_size(item['bytes'])}, stato={item['status']}, WAV={item['wav']}, metadata={item['metadata']}; {kinds}")


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="storycast-work")
    command.add_argument("command", choices=("clean-work", "work-status"))
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--yes", action="store_true")
    command.add_argument("--details", action="store_true")
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "clean-work":
            if args.details: raise StorycastError("--details è valido soltanto con work-status")
            print_clean(clean_work(ROOT, dry_run=args.dry_run, yes=args.yes))
        else:
            if args.dry_run or args.yes: raise StorycastError("--dry-run e --yes non sono validi con work-status")
            print_status(work_status(ROOT, details=args.details))
        return 0
    except (StorycastError, OSError, ValueError) as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

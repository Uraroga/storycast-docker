from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import stat
import sys
from pathlib import Path
from typing import Any

from .core import StorycastError

ROOT = Path(os.environ.get("STORYCAST_ROOT", Path(__file__).resolve().parents[1])).resolve()
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
RUNTIME_ROOTS = ("output", "work", "logs")
PRESERVED_INPUTS = {"MODELLO_DIALOGO.txt", ".gitkeep"}
PROTECTED_TOP = {"assets", "config", "storycast", "scripts", "tests", "docs", "models", "input", ".git"}
PROTECTED_FILES = {"AGENTS.md", "README.md", "avvia-storycast.sh", "docker-compose.yml",
                   "Dockerfile", "Dockerfile.tts", "Dockerfile.renderer",
                   "prompt_fisso_chatgpt.txt", "prompt_fisso_chatgpt_short.txt"}


def _inside(root: Path, path: Path) -> Path:
    root = root.resolve(); absolute = path.absolute()
    try: absolute.relative_to(root)
    except ValueError as exc: raise StorycastError(f"Percorso esterno al progetto rifiutato: {path}") from exc
    if absolute == root: raise StorycastError("La root del progetto è protetta")
    return absolute


def _category(path: Path) -> str:
    value = path.as_posix().lower()
    if "metadata" in path.parts or path.name == "state.json": return "metadata_state"
    if "cache" in value or "derived" in path.parts: return "cache"
    if path.suffix.lower() in {".tmp", ".part", ".lock", ".pid"} or "verification_frames" in value: return "temporary"
    if "scene" in value: return "scene"
    if "backup" in value: return "backup"
    if path.suffix.lower() == ".wav": return "wav"
    if path.suffix.lower() in {".mp4", ".mkv", ".webm"}: return "video"
    if path.parts and path.parts[0] == "input": return "input"
    if path.parts and path.parts[0] == "output": return "output"
    if path.parts and path.parts[0] == "logs": return "log"
    return "runtime"


def _scan(root: Path, targets: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []; seen: set[Path] = set()
    for target in targets:
        target = _inside(root, target)
        if not target.exists() and not target.is_symlink(): continue
        candidates = [target]
        if target.is_dir() and not target.is_symlink():
            candidates = sorted([*target.rglob("*"), target], key=lambda p: (len(p.parts), p.as_posix()), reverse=True)
        for path in candidates:
            absolute = _inside(root, path)
            if absolute in seen: continue
            seen.add(absolute)
            if path.is_symlink():
                resolved = path.resolve(strict=False)
                try: resolved.relative_to(root.resolve())
                except ValueError as exc: raise StorycastError(f"Symlink verso l'esterno rifiutato: {path} -> {resolved}") from exc
            try: info = path.lstat()
            except FileNotFoundError: continue
            is_dir = stat.S_ISDIR(info.st_mode) and not path.is_symlink()
            rows.append({"path": path.relative_to(root).as_posix(), "type": "directory" if is_dir else "file",
                         "bytes": 0 if is_dir else info.st_size, "category": "directory" if is_dir else _category(path.relative_to(root))})
    return sorted(rows, key=lambda row: row["path"])


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file(): return {}
    try: data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise StorycastError(f"state.json non leggibile: {path}") from exc
    return data if isinstance(data, dict) else {}


def _validate_slug(slug: str) -> None:
    if not SLUG_RE.fullmatch(slug): raise StorycastError("Slug non valido o path traversal rilevato")


def _pid_active(lock: Path) -> bool:
    try: data=json.loads(lock.read_text(encoding="utf-8")); pid=data.get("pid")
    except (OSError,json.JSONDecodeError): return False
    if not isinstance(pid,int) or pid <= 0: return False
    try: os.kill(pid,0)
    except ProcessLookupError: return False
    except PermissionError: return True
    return True


def _assert_no_active_generation(root: Path, only_slug: str | None = None) -> None:
    locks = [root/"work/episodes"/only_slug/"run.lock"] if only_slug else list((root/"work/episodes").glob("*/run.lock"))
    active=[p for p in locks if p.is_file() and _pid_active(p)]
    if active: raise StorycastError("Generazione attiva: " + ", ".join(p.relative_to(root).as_posix() for p in active))


def _story_targets(root: Path, slug: str) -> tuple[list[Path], dict[str,Any]]:
    _validate_slug(slug); work=root/"work/episodes"/slug; output=root/"output"/slug
    state=_load_state(work/"state.json"); targets=[work,output]
    for folder in (root/"logs", root/"output", root/"work"):
        if folder.is_dir():
            targets.extend(p for p in folder.glob(f"*{slug}*") if p not in {work,output})
    exists=any(p.exists() or p.is_symlink() for p in targets)
    if not exists: raise StorycastError(f"Storia inesistente: {slug}")
    return list(dict.fromkeys(targets)),state


def _reset_targets(root: Path) -> list[Path]:
    targets=[]
    for name in RUNTIME_ROOTS:
        folder=root/name
        if not folder.exists(): continue
        for child in folder.iterdir():
            targets.append(child)
    return targets


def _preserved(root: Path) -> list[str]:
    values=[]
    for name in sorted(PROTECTED_TOP|PROTECTED_FILES):
        path=root/name
        if path.exists(): values.append(name + ("/" if path.is_dir() else ""))
    for name in sorted(PRESERVED_INPUTS):
        if (root/"input"/name).exists(): values.append(f"input/{name}")
    return values


def inventory(root: Path, mode: str, slug: str | None = None) -> dict[str,Any]:
    root=root.resolve()
    if mode=="story": targets,state=_story_targets(root,slug or "")
    else: targets,state=_reset_targets(root),{}
    rows=_scan(root,targets); files=[x for x in rows if x["type"]=="file"]
    by_category={}
    for row in files: by_category[row["category"]]=by_category.get(row["category"],0)+row["bytes"]
    return {"mode":mode,"slug":slug,"state_used":bool(state),"entries":rows,"files":len(files),
            "directories":sum(x["type"]=="directory" for x in rows),"bytes_total":sum(x["bytes"] for x in files),
            "bytes_by_category":dict(sorted(by_category.items())),"preserved":_preserved(root)}


def _delete_rows(root: Path, rows: list[dict[str,Any]]) -> tuple[int,int]:
    files=directories=0
    paths=sorted((_inside(root,root/row["path"]) for row in rows),key=lambda p:len(p.parts),reverse=True)
    for path in paths:
        if path.is_symlink() or path.is_file(): path.unlink(missing_ok=True); files+=1
        elif path.is_dir():
            try: path.rmdir(); directories+=1
            except OSError as exc: raise StorycastError(f"Directory non vuota o cambiata durante la cancellazione: {path}") from exc
    return files,directories


def execute(root: Path, mode: str, slug: str | None, yes: bool) -> dict[str,Any]:
    _assert_no_active_generation(root,slug if mode=="story" else None)
    report=inventory(root,mode,slug); report["dry_run"]=not yes
    if not yes: report["deleted"]={"files":0,"directories":0}; return report
    deleted=_delete_rows(root,report["entries"])
    for name in RUNTIME_ROOTS: (root/name).mkdir(parents=True,exist_ok=True)
    (root/"work/episodes").mkdir(parents=True,exist_ok=True)
    report["deleted"]={"files":deleted[0],"directories":deleted[1]}
    report["remaining_runtime_entries"]=inventory(root,"reset")["entries"] if mode=="reset" else []
    return report


def print_clean_summary(report: dict[str, Any]) -> None:
    action = "DRY-RUN STORYCAST CLEAN" if report["dry_run"] else "STORYCAST CLEAN"
    print(f"\n{action}\n")
    if report["dry_run"]:
        print(f"File da eliminare: {report['files']}")
        print(f"Directory da svuotare: {report['directories']}")
        print("Nessuna eliminazione eseguita.\n")
    else:
        print("output: pulito\nwork: pulito\nlogs: pulito\n")
    print("input: preservato\nassets: preservati\nprompt: preservati\nmotore: preservato")


def space_status(root: Path) -> dict[str,Any]:
    report=inventory(root,"reset"); episodes=root/"work/episodes"
    stories=sum(p.is_dir() and not p.is_symlink() for p in episodes.iterdir()) if episodes.is_dir() else 0
    categories=report["bytes_by_category"]
    return {"stories":stories,"output_bytes":categories.get("output",0)+categories.get("video",0),
            "wav_bytes":categories.get("wav",0),"cache_bytes":categories.get("cache",0),
            "temporary_bytes":categories.get("temporary",0)+categories.get("scene",0),
            "total_deletable_bytes":report["bytes_total"]}


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog="storycast-pulizia"); p.add_argument("command",choices=("elimina-storia","azzera-lavori","spazio-lavori"))
    p.add_argument("--nome"); p.add_argument("--dry-run",action="store_true"); p.add_argument("--yes",action="store_true"); return p


def main(argv=None) -> int:
    args=parser().parse_args(argv)
    try:
        if args.dry_run and args.yes: raise StorycastError("Usare --dry-run oppure --yes, non entrambi")
        if args.command=="spazio-lavori": result=space_status(ROOT)
        else:
            if args.command=="elimina-storia" and not args.nome: raise StorycastError("--nome SLUG è obbligatorio")
            if args.command=="azzera-lavori" and args.nome: raise StorycastError("--nome non è ammesso per azzera-lavori")
            if not args.yes and not args.dry_run: raise StorycastError("Nessuna cancellazione: usare prima --dry-run oppure --yes")
            result=execute(ROOT,"story" if args.command=="elimina-storia" else "reset",args.nome,args.yes)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if args.command != "spazio-lavori": print_clean_summary(result)
        return 0
    except (StorycastError,OSError,ValueError) as exc: print(f"ERRORE: {exc}",file=sys.stderr); return 1


if __name__=="__main__": raise SystemExit(main())

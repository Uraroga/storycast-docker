from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
LOG_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*_\d{8}_\d{6}(?:_\d+)?\.log$")


class RunLogger:
    """Log sintetico di una singola esecuzione, persistente e senza dipendenze."""

    def __init__(self, root: Path, slug: str, input_name: str, *,
                 clock: Callable[[], datetime] | None = None,
                 level: str | None = None, keep: int | None = None,
                 console: bool = True):
        self.root = root
        self.directory = root / "logs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.clock = clock or _local_now
        configured = (level or os.environ.get("STORYCAST_LOG_LEVEL", "INFO")).upper()
        self.threshold = LEVELS.get(configured, LEVELS["INFO"])
        self.keep = max(1, keep if keep is not None else _positive_int(os.environ.get("STORYCAST_LOG_KEEP"), 20))
        self.console = console
        self.phase = "avvio"
        safe_slug = re.sub(r"[^a-z0-9_-]+", "_", slug.lower()).strip("_-") or "storycast"
        stamp = self.clock().strftime("%Y%m%d_%H%M%S")
        candidate = self.directory / f"{safe_slug}_{stamp}.log"
        suffix = 1
        while candidate.exists():
            candidate = self.directory / f"{safe_slug}_{stamp}_{suffix}.log"
            suffix += 1
        self.path = candidate
        self.path.touch(mode=0o644, exist_ok=False)
        self.path.chmod(0o644)
        self._update_latest()
        self._retain()
        self.info("Avvio Storycast")
        self.info(f"Episodio: {safe_slug}")
        self.info(f"Input: {input_name}")

    def set_phase(self, phase: str) -> None:
        self.phase = phase

    def debug(self, message: str) -> None: self._write("DEBUG", message)
    def info(self, message: str) -> None: self._write("INFO", message)
    def warning(self, message: str) -> None: self._write("WARNING", message)
    def error(self, message: str) -> None: self._write("ERROR", message)
    def record(self, message: str) -> None: self._write("INFO", message, emit_console=False)

    def _write(self, level: str, message: str, *, emit_console: bool = True) -> None:
        if LEVELS[level] < self.threshold:
            return
        clean = " ".join(str(message).replace("\x00", "").splitlines()).strip()
        line = f"[{self.clock().strftime('%Y-%m-%d %H:%M:%S')}] {level:<7} {clean}\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
        if self.console and emit_console and level != "DEBUG":
            print(line.rstrip(), file=sys.stderr if level == "ERROR" else sys.stdout, flush=True)

    def _update_latest(self) -> None:
        latest = self.directory / "latest.log"
        temporary = self.directory / ".latest.log.tmp"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(self.path.name)
        os.replace(temporary, latest)

    def _retain(self) -> None:
        owned = sorted((p for p in self.directory.iterdir() if p.is_file() and not p.is_symlink() and LOG_NAME.fullmatch(p.name)), key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
        for old in owned[self.keep:]:
            old.unlink()


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
        return parsed if parsed > 0 else default
    except ValueError:
        return default


def _local_now() -> datetime:
    name=os.environ.get("STORYCAST_TIMEZONE","Europe/Rome")
    try: return datetime.now(ZoneInfo(name))
    except ZoneInfoNotFoundError: return datetime.now().astimezone()


def format_duration(seconds: float) -> str:
    total = max(0, round(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

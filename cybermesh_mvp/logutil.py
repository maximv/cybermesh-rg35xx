"""Append-only file logger — works even when stdout redirect fails."""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

_LOG_PATH: Optional[Path] = None


def init_bootlog(port_dir: Path) -> Path:
    global _LOG_PATH
    _LOG_PATH = port_dir / "cybermesh.log"
    write(f"==== boot {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} ====")
    return _LOG_PATH


def write(msg: str) -> None:
    if _LOG_PATH is None:
        return
    line = f"[{time.strftime('%H:%M:%S')}] {msg}\n"
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:  # noqa: BLE001
        try:
            sys.stderr.write(line)
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass


def make_logger(port_dir: Optional[Path] = None):
    if port_dir is not None and _LOG_PATH is None:
        init_bootlog(port_dir)

    def log(msg: str) -> None:
        write(msg)

    return log


def log_exception(label: str) -> None:
    write(f"{label}: {traceback.format_exc()}")


def release_pidfile(port_dir: Path) -> None:
    for name in ("cybermesh.pid", "meshtastic.pid"):
        try:
            (port_dir / name).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def acquire_pidfile(port_dir: Path, log) -> bool:
    for name in ("cybermesh.pid", "meshtastic.pid"):
        pidfile = port_dir / name
        if not pidfile.exists():
            continue
        try:
            old = int(pidfile.read_text(encoding="utf-8").strip())
            os.kill(old, 0)
            log(f"already running pid={old}, exit")
            return False
        except (OSError, ValueError):
            log(f"removing stale pidfile {name}")
            pidfile.unlink(missing_ok=True)
    pidfile = port_dir / "cybermesh.pid"
    pid = os.getpid()
    try:
        pidfile.write_text(str(pid), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return True


def enable_faulthandler() -> None:
    if _LOG_PATH is None:
        return
    try:
        import faulthandler

        fh = open(_LOG_PATH, "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(file=fh, all_threads=True)
    except Exception:  # noqa: BLE001
        pass

#!/usr/bin/env python3
"""Cybermesh BLE client for Anbernic RG35xx (stock Linux)."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path


def _port_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    port_dir = _port_dir()
    # Boot log before any cybermesh imports (may fail on partial deploy).
    sys.path.insert(0, str(port_dir))
    pylibs = port_dir / "pylibs"
    if pylibs.is_dir():
        sys.path.insert(0, str(pylibs))

    from cybermesh.logutil import (
        acquire_pidfile,
        enable_faulthandler,
        init_bootlog,
        log_exception,
        make_logger,
        release_pidfile,
    )

    init_bootlog(port_dir)
    enable_faulthandler()
    log = make_logger()

    log(f"python {sys.version.split()[0]} cwd={os.getcwd()}")
    log(f"port_dir={port_dir}")

    if not acquire_pidfile(port_dir, log):
        return 0

    try:
        from cybermesh.radio import RadioManager
    except Exception:  # noqa: BLE001
        log_exception("import RadioManager failed")
        return 1

    parser = argparse.ArgumentParser()
    parser.add_argument("--tui", action="store_true", help="Curses UI (SSH debug)")
    args = parser.parse_args()

    try:
        radio = RadioManager(log=log, port_dir=port_dir)
    except Exception:  # noqa: BLE001
        log_exception("RadioManager init failed")
        return 1

    log("RadioManager ready")

    try:
        if args.tui:
            import curses

            from cybermesh.gamepad import GamepadReader
            from cybermesh.ui import run_ui

            if not sys.stdin.isatty():
                log("TUI needs a TTY")
                return 1

            pad = None

            def runner(stdscr) -> None:
                nonlocal pad
                pad = GamepadReader(lambda ch: curses.ungetch(ch))
                pad.start()
                run_ui(stdscr, radio, port_dir)

            try:
                curses.wrapper(runner)
            except SystemExit:
                pass
            finally:
                if pad is not None:
                    pad.stop()
                radio.disconnect()
            return 0

        log("starting GUI (system SDL2 / mali)")
        try:
            from cybermesh.fbui import run_fbui

            return run_fbui(radio, port_dir, log=log)
        except Exception:  # noqa: BLE001
            log_exception("GUI failed")
            try:
                radio.disconnect()
            except Exception:  # noqa: BLE001
                pass
            return 1
    finally:
        release_pidfile(port_dir)


if __name__ == "__main__":
    port_dir = _port_dir()
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001 — last-resort if logutil itself breaks
        crash = port_dir / "cybermesh.log"
        try:
            with open(crash, "a", encoding="utf-8") as fh:
                fh.write(f"TOP-LEVEL CRASH:\n{traceback.format_exc()}\n")
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:  # noqa: BLE001
            pass
        raise

#!/usr/bin/env python3
"""Print evdev key/button codes as you press them. No third-party deps.

Run on the handheld (over SSH):

    python3 scripts/probe-keys.py

Press the button you want to identify (e.g. M / MENU) and read the `code=` value.
Then map it with, in Cybermesh.sh or the environment:

    export CYBERMESH_MENU_CODE=<code>

Ctrl-C to quit.
"""

from __future__ import annotations

import glob
import select
import struct
import sys

# struct input_event { struct timeval time; __u16 type; __u16 code; __s32 value; }
# Native alignment/size handles both 32-bit and 64-bit (long size differs).
_FMT = "@llHHi"
_SIZE = struct.calcsize(_FMT)

EV_KEY = 0x01
EV_ABS = 0x03

_VALUE = {0: "UP", 1: "DOWN", 2: "REPEAT"}


def _open_devices() -> dict:
    fds = {}
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            fds[open(path, "rb", buffering=0)] = path
        except OSError as exc:
            print(f"skip {path}: {exc}")
    return fds


def main() -> int:
    fds = _open_devices()
    if not fds:
        print("No /dev/input/event* devices (try sudo / root).")
        return 1
    print(f"Listening on {len(fds)} device(s). Press buttons (Ctrl-C to quit).")
    print("Look for EV_KEY on the button you want — that 'code' is what to map.\n")
    try:
        while True:
            ready, _, _ = select.select(list(fds), [], [], 1.0)
            for fh in ready:
                data = fh.read(_SIZE)
                if not data or len(data) < _SIZE:
                    continue
                _s, _us, etype, code, value = struct.unpack(_FMT, data)
                if etype == EV_KEY:
                    label = _VALUE.get(value, str(value))
                    print(f"{fds[fh]:<22} EV_KEY   code={code:<5} {label}")
                elif etype == EV_ABS:
                    # Useful for d-pad/hat; comment out if too noisy.
                    print(f"{fds[fh]:<22} EV_ABS   code={code:<5} value={value}")
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        for fh in fds:
            try:
                fh.close()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

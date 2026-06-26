#!/bin/bash
# One-time setup over SSH. Run from the port folder on the handheld.
set -euo pipefail

PORTDIR="$(cd "$(dirname "$0")" && pwd)"
PYLIBS="$PORTDIR/pylibs"
cd "$PORTDIR"

if ! command -v python3 >/dev/null; then
  echo "python3 not found"
  exit 1
fi

# exFAT/FAT32 SD cards cannot create symlinks — venv will always fail there.
# Install wheels into pylibs/ and use system python3 instead.
rm -rf venv
rm -rf "$PYLIBS"
mkdir -p "$PYLIBS"

echo "Installing Python packages into pylibs/ (no venv — exFAT-safe)..."

if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "Bootstrapping pip..."
  python3 -m ensurepip --upgrade || true
fi

python3 -m pip install --upgrade pip
python3 -m pip install --target "$PYLIBS" -r requirements.txt

chmod +x Cybermesh.sh

cat <<EOF

Dependencies installed in: $PYLIBS

Test import:
  PYTHONPATH=$PYLIBS python3 -c "import pygame, meshtastic; print('OK')"

Before first BLE connect, pair the Heltec once (replace MAC):

  bluetoothctl
  power on
  agent on
  default-agent
  scan on
  pair AA:BB:CC:DD:EE:FF
  trust AA:BB:CC:DD:EE:FF
  quit

If asked for a PIN, try 123456 or check the radio screen.

Rescan games/ports in the launcher, then run Cybermesh.

If the screen stays black, try over SSH:
  SDL_VIDEODRIVER=fbcon ./Cybermesh.sh
  SDL_VIDEODRIVER=directfb ./Cybermesh.sh

EOF

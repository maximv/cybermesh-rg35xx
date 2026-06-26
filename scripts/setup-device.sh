#!/bin/bash
# First-time install on Anbernic RG35xx (run over SSH on the handheld).
set -euo pipefail

GIT_URL="${1:-}"
PORTS_ROOT="${PORTS_ROOT:-/mnt/mmc/Roms/PORTS}"
APP_DIR="$PORTS_ROOT/Cybermesh"

if [ -z "$GIT_URL" ]; then
  echo "Usage: $0 <git-clone-url>"
  echo "Example: $0 https://github.com/maximv/cybermesh-rg35xx.git"
  exit 1
fi

pkill -f cybermesh_mvp.main 2>/dev/null || true
pkill -f meshtastic_mvp.main 2>/dev/null || true

rm -rf "$APP_DIR" "$PORTS_ROOT/Meshtastic"
rm -f "$PORTS_ROOT/Meshtastic.sh"

mkdir -p "$PORTS_ROOT"
git clone "$GIT_URL" "$APP_DIR"

cd "$APP_DIR"
chmod +x Cybermesh.sh install_deps.sh scripts/*.sh 2>/dev/null || true
./scripts/update-on-device.sh

echo ""
echo "Done. Rescan PORTS in the launcher and run Cybermesh."

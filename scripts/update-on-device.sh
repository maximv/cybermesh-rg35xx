#!/bin/bash
# Update already cloned repo on Anbernic (git pull; deps only if needed).
set -euo pipefail

PORTDIR="$(cd "$(dirname "$0")/.." && pwd)"
PORTS_ROOT="$(cd "$PORTDIR/.." && pwd)"

cd "$PORTDIR"

if [ -d .git ]; then
  git pull --ff-only
fi

chmod +x Cybermesh.sh install_deps.sh scripts/*.sh 2>/dev/null || true
"$PORTDIR/scripts/ensure-deps.sh"

if [ -f "$PORTDIR/PORTS-Cybermesh.sh" ]; then
  cp "$PORTDIR/PORTS-Cybermesh.sh" "$PORTS_ROOT/Cybermesh.sh"
  chmod +x "$PORTS_ROOT/Cybermesh.sh"
fi

echo "Updated: $PORTDIR"

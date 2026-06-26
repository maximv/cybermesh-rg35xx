#!/bin/bash
# Copy this file to: /mnt/mmc/Roms/PORTS/Cybermesh.sh
# (PORTS menu entry — NOT the copy inside Cybermesh/ subfolder)
ROOT="$(cd "$(dirname "$0")" && pwd)"
APP=""
if [ -d "$ROOT/Cybermesh" ]; then
  APP="$(cd "$ROOT/Cybermesh" && pwd)"
fi
LOG="${APP:-$ROOT}/cybermesh.log"

echo "==== $(date) PORTS menu entry ====" >> "$LOG"
echo "APP=$APP" >> "$LOG"

if [ -z "$APP" ]; then
  echo "[$(date +%H:%M:%S)] FATAL: no Cybermesh/ folder" >> "$LOG"
  sleep 5
  exit 1
fi

pkill -f "cybermesh.main" 2>/dev/null || true
pkill -f "cybermesh_mvp.main" 2>/dev/null || true  # kill stale pre-rename process
sleep 1

if [ -x "$APP/Cybermesh.sh" ]; then
  cd "$APP" || exit 1
  exec "./Cybermesh.sh"
fi

echo "[$(date +%H:%M:%S)] FATAL: missing Cybermesh.sh in $APP" >> "$LOG"
sleep 5
exit 1

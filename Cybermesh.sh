#!/bin/bash
# Inner launcher — lives in Cybermesh/ folder.
PORTDIR="$(cd "$(dirname "$0")" && pwd)"
PYLIBS="$PORTDIR/pylibs"
LOG="$PORTDIR/cybermesh.log"
VER="0.1b"

log_line() {
  echo "[$(date +%H:%M:%S)] $*" >> "$LOG"
}

{
  echo "==== $(date) Cybermesh.sh v$VER ===="
  echo "PORTDIR=$PORTDIR"
  echo "PPID=$PPID"
} >> "$LOG" 2>&1

pkill -f "cybermesh.main" 2>/dev/null || true
sleep 0.5

if [ ! -d "$PYLIBS" ]; then
  log_line "FATAL: missing pylibs/"
  sleep 5
  exit 1
fi

export PYTHONUNBUFFERED=1
export PYTHONPATH="$PYLIBS:$PORTDIR${PYTHONPATH:+:$PYTHONPATH}"
export SDL_VIDEODRIVER=mali
export SDL_AUDIODRIVER="${SDL_AUDIODRIVER:-dummy}"

cd "$PORTDIR" || exit 1
log_line "python3 -m cybermesh.main (build $VER)"
python3 -m cybermesh.main >>"$LOG" 2>&1
EC=$?
log_line "python exited code=$EC"
exit "$EC"

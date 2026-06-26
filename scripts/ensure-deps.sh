#!/bin/bash
# Install pylibs only when missing, broken, or requirements.txt changed.
set -euo pipefail

PORTDIR="$(cd "$(dirname "$0")/.." && pwd)"
PYLIBS="$PORTDIR/pylibs"
REQ="$PORTDIR/requirements.txt"
STAMP="$PORTDIR/.pylibs-requirements.sha256"

_hash_req() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$REQ" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$REQ" | awk '{print $1}'
  else
    wc -c < "$REQ" | tr -d ' '
  fi
}

_pylibs_ok() {
  [ -d "$PYLIBS" ] || return 1
  [ -n "$(ls -A "$PYLIBS" 2>/dev/null || true)" ] || return 1
  PYTHONPATH="$PYLIBS" python3 -c "import meshtastic" >/dev/null 2>&1 || return 1
  return 0
}

if [ ! -f "$REQ" ]; then
  if _pylibs_ok; then
    echo "pylibs OK"
    exit 0
  fi
  echo "requirements.txt missing and pylibs not usable"
  exit 1
fi

cur="$(_hash_req)"

if _pylibs_ok; then
  if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$cur" ]; then
    echo "pylibs OK — skip install (requirements unchanged)"
    exit 0
  fi
  if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" != "$cur" ]; then
    echo "requirements.txt changed — reinstalling..."
    "$PORTDIR/install_deps.sh"
    echo "$cur" > "$STAMP"
    exit 0
  fi
  # pylibs already fine (e.g. upgraded from older scripts) — just record stamp
  echo "$cur" > "$STAMP"
  echo "pylibs OK — skip install (stamp created)"
  exit 0
fi

echo "Installing Python deps into pylibs/..."
"$PORTDIR/install_deps.sh"
echo "$cur" > "$STAMP"

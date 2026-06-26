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

_needs_install() {
  if ! _pylibs_ok; then
    return 0
  fi
  if [ ! -f "$REQ" ]; then
    return 1
  fi
  cur="$(_hash_req)"
  if [ ! -f "$STAMP" ]; then
    return 0
  fi
  saved="$(cat "$STAMP")"
  [ "$cur" != "$saved" ]
}

if _needs_install; then
  echo "Installing Python deps into pylibs/..."
  "$PORTDIR/install_deps.sh"
  if [ -f "$REQ" ]; then
    _hash_req > "$STAMP"
  fi
else
  echo "pylibs OK — skip install (requirements unchanged)"
fi

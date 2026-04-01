#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"

quiet=0
force=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet)
      quiet=1
      shift
      ;;
    --force)
      force=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

SKILL_DIR="$(cd -P -- "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$(youtrack_skill_venv_dir "$SKILL_DIR")"
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"
PYTHON_BIN="$(choose_python_interpreter)"

if [[ $force -eq 1 && -d "$VENV_DIR" ]]; then
  rm -rf -- "$VENV_DIR"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  [[ $quiet -eq 1 ]] || echo "Creating virtual environment in $VENV_DIR" >&2
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

PIP_ARGS=(--disable-pip-version-check)
if [[ $quiet -eq 1 ]]; then
  PIP_ARGS+=(-q)
fi

"$VENV_DIR/bin/python" -m pip install "${PIP_ARGS[@]}" --upgrade pip
"$VENV_DIR/bin/python" -m pip install "${PIP_ARGS[@]}" -r "$REQUIREMENTS_FILE"

if [[ $quiet -eq 0 ]]; then
  "$VENV_DIR/bin/python" - <<'PY'
import sys
print(f"skill-youtrack ready with Python {sys.version.split()[0]}")
PY
fi

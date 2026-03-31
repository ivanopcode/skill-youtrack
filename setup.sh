#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SKILL_DIR/scripts/common.sh"

PYTHON_BIN="$(choose_python_interpreter)"

exec "$PYTHON_BIN" "$SKILL_DIR/scripts/setup_main.py" "$@"

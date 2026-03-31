#!/usr/bin/env bash
set -euo pipefail

resolve_script_path() {
  local source_path="$1"
  while [[ -L "$source_path" ]]; do
    local dir
    dir="$(cd -P -- "$(dirname -- "$source_path")" && pwd)"
    source_path="$(readlink "$source_path")"
    [[ "$source_path" != /* ]] && source_path="$dir/$source_path"
  done
  cd -P -- "$(dirname -- "$source_path")" && pwd
}

youtrack_skill_dir() {
  local script_dir
  script_dir="$(resolve_script_path "${BASH_SOURCE[1]}")"
  cd -P -- "$script_dir/.." && pwd
}

youtrack_skill_venv_dir() {
  local skill_dir="$1"
  printf '%s/.venv\n' "$skill_dir"
}

choose_python_interpreter() {
  local candidates=()
  if [[ -n "${YOUTRACK_CLI_PYTHON:-}" ]]; then
    candidates+=("${YOUTRACK_CLI_PYTHON}")
  fi
  if [[ -n "${YTX_PYTHON:-}" ]]; then
    candidates+=("${YTX_PYTHON}")
  fi
  candidates+=(python3.13 python3.12 python3.11 python3.10 python3 python)

  local candidate resolved
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      resolved="$candidate"
    elif command -v "$candidate" >/dev/null 2>&1; then
      resolved="$(command -v "$candidate")"
    else
      continue
    fi

    if "$resolved" - <<'PY' >/dev/null 2>&1
import sys
major, minor = sys.version_info[:2]
raise SystemExit(0 if major == 3 and 10 <= minor < 14 else 1)
PY
    then
      printf '%s\n' "$resolved"
      return 0
    fi
  done

  cat >&2 <<'EOF'
No supported Python interpreter found for the youtrack-cli skill.
Expected Python 3.10-3.13. Python 3.14 is intentionally skipped because the tested youtrack-cli stack is not known-good there.
Set YOUTRACK_CLI_PYTHON to an explicit interpreter path if needed.
EOF
  return 1
}

ensure_skill_venv() {
  local skill_dir="$1"
  local venv_dir
  venv_dir="$(youtrack_skill_venv_dir "$skill_dir")"
  if [[ -x "$venv_dir/bin/python" && -x "$venv_dir/bin/yt" ]]; then
    return 0
  fi
  "$skill_dir/scripts/bootstrap.sh" --quiet
}

#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="$(basename -- "$SKILL_DIR")"

bootstrap_usage() {
  cat <<EOF
Usage: $0

Bootstraps the committed repo-local $SKILL_NAME runtime:
- refreshes <repo>/.claude/skills/$SKILL_NAME
- installs runtime dependencies into <repo>/.agents/skills/$SKILL_NAME/.venv
- refreshes <repo>/.agents/bin/{yt,ytx} and <repo>/.agents/env.sh
EOF
}

ensure_repo_command_layer() {
  local repo_root="$1"
  local repo_bin_dir="$repo_root/.agents/bin"
  local env_path="$repo_root/.agents/env.sh"

  mkdir -p -- "$repo_bin_dir"

  if [[ -L "$repo_bin_dir/yt" || -f "$repo_bin_dir/yt" ]]; then
    rm -f -- "$repo_bin_dir/yt"
  elif [[ -e "$repo_bin_dir/yt" ]]; then
    echo "Refusing to replace existing directory: $repo_bin_dir/yt" >&2
    exit 1
  fi
  ln -s "../skills/$SKILL_NAME/scripts/yt" "$repo_bin_dir/yt"

  if [[ -L "$repo_bin_dir/ytx" || -f "$repo_bin_dir/ytx" ]]; then
    rm -f -- "$repo_bin_dir/ytx"
  elif [[ -e "$repo_bin_dir/ytx" ]]; then
    echo "Refusing to replace existing directory: $repo_bin_dir/ytx" >&2
    exit 1
  fi
  ln -s "../skills/$SKILL_NAME/scripts/ytx" "$repo_bin_dir/ytx"

  cat >"$env_path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if ! REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  echo "source .agents/env.sh from inside the repository" >&2
  return 1 2>/dev/null || exit 1
fi

export PATH="$REPO_ROOT/.agents/bin:$REPO_ROOT/.agents/skills/skill-youtrack/.venv/bin:${PATH}"
unset REPO_ROOT
EOF
  chmod 755 "$env_path"
}

source_mode() {
  exec python3 "$SKILL_DIR/scripts/setup_main.py" "$@"
}

bootstrap_mode() {
  if [[ $# -gt 0 ]]; then
    case "$1" in
      -h|--help)
        bootstrap_usage
        exit 0
        ;;
      *)
        echo "Repo bootstrap mode does not accept install target arguments." >&2
        bootstrap_usage >&2
        exit 1
        ;;
    esac
  fi

  local repo_root
  if ! repo_root="$(git -C "$SKILL_DIR" rev-parse --show-toplevel 2>/dev/null)"; then
    echo "Repo bootstrap mode must run from a committed skill copy inside a git repository." >&2
    exit 1
  fi

  local expected_skill_dir="$repo_root/.agents/skills/$SKILL_NAME"
  if [[ "$(cd -P -- "$SKILL_DIR" && pwd)" != "$expected_skill_dir" ]]; then
    echo "Expected the committed skill copy at $expected_skill_dir" >&2
    exit 1
  fi

  local claude_link="$repo_root/.claude/skills/$SKILL_NAME"
  local link_value="../../.agents/skills/$SKILL_NAME"

  if [[ -L "$claude_link" || -f "$claude_link" ]]; then
    rm -f -- "$claude_link"
  elif [[ -e "$claude_link" ]]; then
    echo "Refusing to replace existing directory: $claude_link" >&2
    exit 1
  fi

  mkdir -p -- "$(dirname -- "$claude_link")"
  ln -s "$link_value" "$claude_link"

  "$SKILL_DIR/scripts/bootstrap.sh" --quiet
  ensure_repo_command_layer "$repo_root"

  cat <<EOF
Bootstrapped $SKILL_NAME
  Project copy: $SKILL_DIR
  Claude skill link: $claude_link
  Repo bin: $repo_root/.agents/bin
  Shell env: source $repo_root/.agents/env.sh
EOF
}

if [[ -e "$SKILL_DIR/.git" ]]; then
  source_mode "$@"
else
  bootstrap_mode "$@"
fi

---
name: youtrack-cli
description: Use when working with one or more self-hosted or cloud YouTrack instances through a local CLI workflow instead of MCP. Covers named instance labels, macOS Keychain-backed auth, board and sprint inspection, issue search, comments, field updates, explicit board/sprint membership changes, and agent-facing JSON reads through the bundled `ytx` wrapper.
---

# YouTrack CLI

## Overview

This skill provides a portable YouTrack CLI runtime inside the skill directory.
Use it when an agent or developer needs deterministic command-line access to YouTrack boards, sprints, issues, comments, and issue mutations without introducing an MCP server.

The skill bundles two entrypoints:

- `scripts/yt` for the upstream `youtrack-cli` project by Ryan Cheley
- `scripts/ytx` for agent-facing JSON operations layered on top of the same credentials

Both wrappers resolve paths relative to the skill location, so they work from any current directory and do not depend on a user-specific `pipx` path.
They share the same multi-instance runtime:

- named `instance` labels such as `primary` or `staging`
- one Keychain service per label
- one config file per label in `~/.config/youtrack-cli/instances/`
- one pinned active instance per installed skill copy

## When To Use

Use this skill when you need one or more of the following:

- inspect agile boards, current sprints, and board-visible issues
- search, view, comment on, or update YouTrack issues
- add or remove issues from explicit board/sprint membership
- resolve the current developer from local git config and map that identity to a YouTrack user
- keep authentication in macOS Keychain instead of shell history, env files, or repo config
- switch cleanly between multiple YouTrack instances without overwriting credentials

Do not use this skill when the user explicitly wants an MCP integration instead of a local CLI workflow.

## Quick Start

1. Install the skill into your agent environments:

```bash
~/agents/skills/youtrack-cli/setup.sh global --locale en
```

For a single repository instead of your home-level agent config:

```bash
~/agents/skills/youtrack-cli/setup.sh local /abs/path/to/repo --locale ru
```

`global` creates a managed install copy outside the source repo and links `~/.claude/skills/youtrack-cli` and `~/.codex/skills/youtrack-cli` to that managed copy.

`local` copies the skill into `<repo>/.skills/youtrack-cli`, removes any nested git metadata from that copy, and then points `<repo>/.claude/skills/youtrack-cli` and `<repo>/.codex/skills/youtrack-cli` at the copied version. That makes the skill part of the project repo instead of a symlink to your home directory.

The source skill stays English. Installed copies render `description` and other user-facing metadata from `locales/metadata.json` for the selected locale mode. Supported locale modes are `en`, `ru`, `en-ru`, and `ru-en`.

2. Authenticate with YouTrack. Login now requires an explicit label:

```bash
~/agents/skills/youtrack-cli/scripts/yt --instance primary auth login --base-url https://your-youtrack-host
```

This stores the token in macOS Keychain under the selected label and auto-pins that label for the current install.

3. Inspect and pin instances:

```bash
~/agents/skills/youtrack-cli/scripts/yt instances list
~/agents/skills/youtrack-cli/scripts/yt instances current
~/agents/skills/youtrack-cli/scripts/yt instances use primary
```

4. Use `yt` for raw CLI coverage and `ytx` for stable JSON reads and common mutations:

```bash
~/agents/skills/youtrack-cli/scripts/yt boards list
~/agents/skills/youtrack-cli/scripts/ytx board issues <board-id> --mine
```

For self-signed or custom CA deployments, pass the same SSL options supported by `youtrack-cli`, for example:

```bash
~/agents/skills/youtrack-cli/scripts/yt --instance primary auth login --base-url https://your-youtrack-host --cert-file /path/to/cert.pem
~/agents/skills/youtrack-cli/scripts/yt --instance primary auth login --base-url https://your-youtrack-host --ca-bundle /path/to/ca-bundle.pem
~/agents/skills/youtrack-cli/scripts/yt --instance primary auth login --base-url https://your-youtrack-host --no-verify-ssl
```

## Runtime Layout

- `setup.sh global` creates a managed install copy under `${XDG_DATA_HOME:-~/.local/share}/agents/skills/youtrack-cli`, bootstraps that copy, renders localized metadata, and links `~/.claude/skills` and `~/.codex/skills` to it
- `setup.sh local <repo>` copies the skill into `<repo>/.skills/youtrack-cli`, strips git metadata from that copy, bootstraps the copied runtime, renders localized metadata, and then links the project's `.claude/skills` and `.codex/skills` to the copied skill
- `scripts/bootstrap.sh` creates `.venv/` inside the installed skill and installs the pinned dependencies from `scripts/requirements.txt`
- `scripts/yt` is a Python-aware wrapper around the upstream MIT-licensed `youtrack-cli` package and injects the selected instance config/keychain context
- `scripts/ytx` runs the bundled Python helper from that environment
- `scripts/ytx.py` is the agent-facing wrapper implementation
- `locales/metadata.json` is the source of truth for installed localized metadata
- per-instance config files live in `~/.config/youtrack-cli/instances/<label>.env`
- per-install pin state lives in `~/.config/youtrack-cli/installs/<install-id>.json`

The wrappers auto-bootstrap if the environment is missing, but `setup.sh` is the preferred first step because it both prepares the runtime and links the skill into the selected agent environments.
Local installs still keep their pinned instance state outside the repo; only the skill code is copied into `<repo>/.skills/youtrack-cli`. Local install locale is project-fixed after the first install. Global install locale may be changed on a later rerun.

## Multi-instance Rules

- `yt auth login` requires `--instance <label>`
- default resolution for commands is: `--instance` > `YOUTRACK_INSTANCE` > pinned active instance for this install > the only registered instance
- use `yt instances use <label>` to pin a default for the current install
- use `YOUTRACK_INSTANCE=<label>` for one shell or one agent run without changing the pin
- use `--no-auto-pin` with `yt --instance <label> auth login` if login should not change the current pin
- legacy single-account `~/.config/youtrack-cli/.env` is intentionally ignored; re-login under an explicit label if you were using the old setup

## Core Workflows

### Read Boards And Sprint Issues

Use `ytx` for JSON-friendly reads:

```bash
~/agents/skills/youtrack-cli/scripts/ytx --instance primary board list
~/agents/skills/youtrack-cli/scripts/ytx --instance primary board show <board-id>
~/agents/skills/youtrack-cli/scripts/ytx --instance primary board sprints <board-id> --current
~/agents/skills/youtrack-cli/scripts/ytx --instance primary board issues <board-id> --source web
```

Default board issue source is `web`, because some explicit/manual agile boards show issue cards in the UI that are not returned by the strict sprint membership endpoint. Read [`references/board-membership.md`](references/board-membership.md) when a board appears inconsistent.

### Filter To The Current Developer

Use `--mine` for board queries:

```bash
~/agents/skills/youtrack-cli/scripts/ytx board issues <board-id> --mine
```

`--mine` resolves the current developer from `git config user.email`, falls back to the global git config, takes the localpart before `@`, and searches YouTrack users with that value. If the match is ambiguous, `ytx` fails with the candidate list instead of guessing.

### Inspect And Mutate Issues

```bash
~/agents/skills/youtrack-cli/scripts/ytx issue show <issue-id>
~/agents/skills/youtrack-cli/scripts/ytx issue search 'id: <issue-id>'
~/agents/skills/youtrack-cli/scripts/ytx issue comment-list <issue-id>
~/agents/skills/youtrack-cli/scripts/ytx issue comment-add <issue-id> 'Текст комментария'
~/agents/skills/youtrack-cli/scripts/ytx issue update <issue-id> --state 'In Progress'
~/agents/skills/youtrack-cli/scripts/ytx issue update <issue-id> --custom-field 'Field Name=Value'
```

For unsupported or project-specific commands, use the raw command interface:

```bash
~/agents/skills/youtrack-cli/scripts/ytx issue command <issue-id> 'add tag important'
```

Use `--dry-run` before risky command mutations:

```bash
~/agents/skills/youtrack-cli/scripts/ytx issue command <issue-id> 'add Board <board-name> <sprint-name>' --dry-run
```

### Manage Board Membership Explicitly

For boards with explicit sprint membership, use:

```bash
~/agents/skills/youtrack-cli/scripts/ytx issue board-add <issue-id> --board '<board-name>' --current-sprint
~/agents/skills/youtrack-cli/scripts/ytx issue board-remove <issue-id> --board '<board-name>' --sprint '<sprint-name>'
```

This compiles down to a YouTrack command such as:

```text
add Board <board-name> <sprint-name>
```

## Safety Rules

- Keep tokens in macOS Keychain via `yt auth login`; do not save them in shell history or checked-in files.
- Always choose an explicit label on first login, for example `primary` or `staging`.
- Prefer `ytx` for reads consumed by agents because it emits compact JSON.
- Prefer `--dry-run` before raw command mutations when the effect is not obvious.
- When a board UI and the strict sprint endpoint disagree, trust `ytx board issues --source web` for "what the user currently sees on the board", and use the strict endpoint only when you specifically need sprint membership.

## References

- [`references/board-membership.md`](references/board-membership.md) for explicit/manual board behavior and `web` vs `strict` reads

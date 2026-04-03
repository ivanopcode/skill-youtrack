<!--
Agent note: This README is a general description of the skill project for maintainers,
contributors, and evaluators. It covers architecture, packaging, and technical details.
Do not use README as operational instructions; use `SKILL.md`.
-->

# skill-youtrack

`skill-youtrack` is a standalone skill and local CLI runtime for working with one or more
JetBrains YouTrack instances from the terminal or from an agent environment.
It is designed for deterministic operational workflows:

- keep credentials in macOS Keychain
- switch cleanly between named YouTrack instances
- inspect boards, sprints, issues, and comments from the CLI
- expose stable agent-facing reads and common mutations through `ytx`

The skill is intentionally layered. It reuses the upstream Python package
`youtrack-cli==0.22.2` and adds a portable runtime, multi-instance auth, and an
agent-oriented wrapper on top.

The upstream CLI comes from the `yt-cli` project by Ryan Cheley and remains the
underlying YouTrack client used by this skill.

The source-of-truth repo stays English. Installed copies render user-facing
metadata from `locales/metadata.json` according to the selected install locale.

## What It Does

- bootstrap a local Python runtime inside the skill directory
- authenticate one or more YouTrack instances under explicit labels
- store secrets per label in macOS Keychain
- keep per-label non-secret config in `~/.config/youtrack-cli/instances/`
- keep optional scoped board ids per label for large instances
- pin an active instance per installed skill copy
- inspect agile boards and current sprints
- read board-visible issues with `web` or strict sprint membership semantics
- expose board-oriented reads such as `board current`, `board my-tasks`, and `board tasks`
- inspect, search, comment on, and update issues
- create issues and subtasks through preview-first `ytx` workflows
- manage explicit board and sprint membership
- resolve the current developer from git config through `--mine`

## Requirements

- macOS
- `python3`
- network access during bootstrap, unless dependencies are already cached locally
- access to one or more YouTrack instances
- `git` when using `--mine`

Bootstrap is not fully offline by default. The skill creates a local virtual
environment and installs `youtrack-cli==0.22.2` and its Python dependencies
with `pip`. After bootstrap, the runtime is local. Ongoing network access is
needed only for YouTrack itself.

Supported install locale modes:

- `en`
- `ru`
- `en-ru`
- `ru-en`

`en-ru` and `ru-en` are experimental. They only make `SKILL.md` frontmatter
`description` bilingual in the installed copy. Other user-facing metadata uses
the primary locale only.

## Repository Structure

- `SKILL.md`
  Agent-facing instructions and usage contract
- `README.md`
  Human-facing documentation for installation, operation, and maintenance
- `locales/metadata.json`
  Install-time translation catalog for user-facing metadata and trigger catalog
- `agents/openai.yaml`
  Skill card metadata rendered during installation
- `setup.sh`
  Dual-mode entrypoint for source installs into one repository and repo-local bootstrap after clone
- `scripts/setup_main.py`, `scripts/setup_support.py`
  Source-install helper for repo-local copies, metadata rendering, and runtime packaging
- `scripts/bootstrap.sh`
  Creates `.venv/` and installs Python dependencies
- `scripts/yt`
  Portable launcher for the upstream CLI through the skill runtime
- `scripts/yt_main.py`
  Wrapper around upstream `youtrack-cli` with instance selection and Keychain routing
- `scripts/ytx`
  Portable launcher for the agent-facing wrapper
- `scripts/ytx.py`
  Agent-facing JSON-friendly operations for boards, issues, comments, and board membership
- `scripts/instance_runtime.py`
  Shared instance registry, per-install pinning, and Keychain service switching
- `references/`
  Supporting documentation for edge cases and behavior notes
- `tests/`
  Unit tests for the wrapper layer

## Architecture

### 1. Skill Layer

`SKILL.md` defines when the skill should be used and which command patterns an
agent should prefer. This is the agent-facing contract.

### 2. Setup And Bootstrap Layer

`setup.sh` installs the skill into one repository when run from the source repo
and bootstraps a committed repo-local runtime when run from an installed copy.
`scripts/bootstrap.sh` prepares the local Python runtime and installs dependencies.

This keeps the skill self-contained at runtime without depending on `pipx` or a
user-specific interpreter path.

### 3. Upstream CLI Layer

The skill uses the published `youtrack-cli==0.22.2` package as the underlying
YouTrack client. This package comes from the `yt-cli` project by Ryan Cheley:

- homepage: `https://github.com/ryancheley/yt-cli`
- license: `MIT`

The skill does not patch that package in place.

Instead, `scripts/yt_main.py` wraps the upstream CLI and injects:

- per-instance config selection
- per-instance Keychain service selection
- optional scoped board ids per instance
- install-local active instance pinning
- instance management commands

### 4. Agent-Facing Layer

`scripts/ytx.py` provides normalized JSON-oriented reads and common mutations on
top of the same auth/runtime context. This is the preferred interface for
agent workflows that need predictable output. On large instances, it should be
used through the scoped board surface rather than unrestricted board discovery.

## Installation

### Install Into One Repository

Use this when a project should carry its own tracked copy of the skill:

```bash
~/agents/skills/skill-youtrack/setup.sh /abs/path/to/repo --locale ru
```

This does the following:

- copies the skill into `<repo>/.agents/skills/skill-youtrack`
- removes nested git metadata from that copied skill
- renders installed metadata in the selected locale
- prunes installer-only files from the committed runtime copy
- bootstraps the copied skill runtime
- links `<repo>/.claude/skills/skill-youtrack` to that copied skill
- prefixes the local skill metadata with a locale-aware local marker so it is distinguishable in skill UIs

The copied skill is intended to be tracked by the project repository.
Its locale is project-fixed on first install. Later reruns reuse the stored
locale. Passing a different locale for that project copy fails instead of
silently rewriting the install metadata.

### Bootstrap After Clone

Once the committed runtime copy already exists inside the repository:

```bash
<repo>/.agents/skills/skill-youtrack/setup.sh
```

This refreshes the project-local `.claude` link and installs runtime
dependencies into `<repo>/.agents/skills/skill-youtrack/.venv`.
It also refreshes:

- `<repo>/.agents/bin/yt`
- `<repo>/.agents/bin/ytx`
- `<repo>/.agents/env.sh`

Project bootstrap scripts can then expose the repo-local commands with:

```bash
source <repo>/.agents/env.sh
```

The source of truth remains the source directory:

```text
~/agents/skills/skill-youtrack
```

The committed runtime copy lives under:

```text
<repo>/.agents/skills/skill-youtrack
```

The source `skill-youtrack` repository itself does not need to contain a root
`AGENTS.md`. Repo install mode only refreshes the copied skill under
the target repository and does not modify other project files.

### Locale Selection Rules

- First install requires explicit `--locale`
- Later reruns may omit `--locale` and reuse the stored install manifest value
- Repo install may not change locale after the first project install

## First Login

Authentication requires an explicit instance label:

```bash
~/agents/skills/skill-youtrack/scripts/yt \
  --instance primary \
  auth login \
  --base-url https://your-youtrack-host
```

This stores credentials under the selected label and auto-pins that label for
the current installed copy of the skill.

On large YouTrack instances, strongly prefer setting scoped boards at login:

```bash
~/agents/skills/skill-youtrack/scripts/yt \
  --instance primary \
  --board-id 83-2561 \
  --board-id agiles/195-1 \
  auth login \
  --base-url https://your-youtrack-host
```

This persists the preferred agile boards in the same per-instance config as the
base URL and SSL settings.

For custom CA or self-signed deployments:

```bash
~/agents/skills/skill-youtrack/scripts/yt \
  --instance primary \
  auth login \
  --base-url https://your-youtrack-host \
  --cert-file /path/to/cert.pem

~/agents/skills/skill-youtrack/scripts/yt \
  --instance primary \
  auth login \
  --base-url https://your-youtrack-host \
  --ca-bundle /path/to/ca-bundle.pem

~/agents/skills/skill-youtrack/scripts/yt \
  --instance primary \
  auth login \
  --base-url https://your-youtrack-host \
  --no-verify-ssl
```

## Instance Model

An instance label is a local identifier for one YouTrack environment.

Examples:

- `primary`
- `staging`
- `client-a`

Selection precedence is:

1. `--instance <label>`
2. `YOUTRACK_INSTANCE=<label>`
3. pinned active instance for the current install
4. the only registered instance, if exactly one exists

Useful commands:

```bash
~/agents/skills/skill-youtrack/scripts/yt instances list
~/agents/skills/skill-youtrack/scripts/yt instances current
~/agents/skills/skill-youtrack/scripts/yt instances use primary
~/agents/skills/skill-youtrack/scripts/yt instances scope set primary 83-2561 195-1
~/agents/skills/skill-youtrack/scripts/yt instances scope clear primary
~/agents/skills/skill-youtrack/scripts/yt instances rename primary main
~/agents/skills/skill-youtrack/scripts/yt --instance primary auth status
~/agents/skills/skill-youtrack/scripts/yt --instance primary auth logout
```

`instances list` and `instances current` expose `scoped_board_ids`. Agents
should treat those ids as the default search surface for that instance.

## Large Instance Routing

On very large self-hosted YouTrack installations, the main failure mode is
starting with `board list` across the whole instance and forcing the agent to
guess which board matters.

The intended narrowing strategy is:

1. Read `yt instances current`
2. If `scoped_board_ids` is non-empty, stay inside that scope
3. Use `ytx board current` or `ytx board my-tasks` before lower-level reads
4. Use `ytx board tasks --assignee ...` or `ytx board tasks --initiator ...` for person-specific board queries
5. Use `ytx board list --scoped` if board metadata is needed
6. Fall back to unrestricted `ytx board list` only when there is no scope

## CLI Usage

### Raw Upstream Coverage Through `yt`

Use `yt` when you want the broader upstream command surface:

```bash
~/agents/skills/skill-youtrack/scripts/yt boards list
~/agents/skills/skill-youtrack/scripts/yt projects list
~/agents/skills/skill-youtrack/scripts/yt issues search "state: Open"
```

### Agent-Friendly Reads Through `ytx`

Use `ytx` when output stability matters:

```bash
~/agents/skills/skill-youtrack/scripts/ytx --instance primary board current
~/agents/skills/skill-youtrack/scripts/ytx --instance primary board my-tasks
~/agents/skills/skill-youtrack/scripts/ytx --instance primary board tasks --assignee "Developer Name"
~/agents/skills/skill-youtrack/scripts/ytx --instance primary board tasks --initiator "Developer Name"
~/agents/skills/skill-youtrack/scripts/ytx --instance primary board list --scoped
~/agents/skills/skill-youtrack/scripts/ytx --instance primary board show <board-id>
~/agents/skills/skill-youtrack/scripts/ytx --instance primary board sprints <board-id> --current
~/agents/skills/skill-youtrack/scripts/ytx --instance primary board issues <board-id> --source web
~/agents/skills/skill-youtrack/scripts/ytx --instance primary board scoped-issues --mine

~/agents/skills/skill-youtrack/scripts/ytx --instance primary issue brief <issue-id>
~/agents/skills/skill-youtrack/scripts/ytx --instance primary issue show <issue-id>
~/agents/skills/skill-youtrack/scripts/ytx --instance primary issue search "id: <issue-id>"
~/agents/skills/skill-youtrack/scripts/ytx --instance primary issue comment-list <issue-id>
```

### Current Developer Resolution

`--mine` resolves the current developer from `git config user.email`:

```bash
~/agents/skills/skill-youtrack/scripts/ytx board issues <board-id> --mine
~/agents/skills/skill-youtrack/scripts/ytx board scoped-issues --mine
```

This is intended for boards that use assignee-based workflows. On large
instances, prefer the scoped variant so the agent never starts from a full
board crawl.

### Issue Mutations

```bash
~/agents/skills/skill-youtrack/scripts/ytx board create-task --summary "Task summary"
~/agents/skills/skill-youtrack/scripts/ytx board create-subtask --parent <issue-id> --summary "Task summary"
~/agents/skills/skill-youtrack/scripts/ytx issue create --project <project-id> --summary "Task summary"
~/agents/skills/skill-youtrack/scripts/ytx issue create-subtask --parent <issue-id> --summary "Task summary"
~/agents/skills/skill-youtrack/scripts/ytx issue link --source <issue-id> --target <issue-id> --type "Subtask"
~/agents/skills/skill-youtrack/scripts/ytx issue comment-add <issue-id> "Comment text"
~/agents/skills/skill-youtrack/scripts/ytx issue update <issue-id> --state "In Progress"
~/agents/skills/skill-youtrack/scripts/ytx issue update <issue-id> --custom-field "Field Name=Value"
~/agents/skills/skill-youtrack/scripts/ytx issue command <issue-id> "add tag important"
```

High-level create and link flows are preview-first. Run them once without `--apply`,
inspect the preview envelope, and then rerun with `--apply`:

```bash
~/agents/skills/skill-youtrack/scripts/ytx board create-subtask \
  --parent <issue-id> \
  --summary "Task summary" \
  --field "Label=Alpha" \
  --current-sprint

~/agents/skills/skill-youtrack/scripts/ytx board create-subtask \
  --parent <issue-id> \
  --summary "Task summary" \
  --field "Label=Alpha" \
  --current-sprint \
  --apply
```

Use `--dry-run` before risky raw command-based mutations:

```bash
~/agents/skills/skill-youtrack/scripts/ytx issue command <issue-id> "add Board <board-name> <sprint-name>" --dry-run
```

For agent usage, the intended behavior is:

1. preview first
2. if the preview matches the user request, run `--apply` in the same turn
3. ask the user only when the preview reveals ambiguity or risk

### Explicit Board Membership

For boards that require manual sprint membership:

```bash
~/agents/skills/skill-youtrack/scripts/ytx issue board-add <issue-id> --board "<board-name>" --current-sprint
~/agents/skills/skill-youtrack/scripts/ytx issue board-remove <issue-id> --board "<board-name>" --sprint "<sprint-name>"
```

## Agent Environment Usage

The skill is intended to be called through the installed skill link in Codex or Claude.
Typical agent prompts should ask for the operation, not for the raw shell syntax.

Examples:

- `Use $skill-youtrack to show the current board context and my tasks for this sprint.`
- `Use $skill-youtrack to list tasks assigned to <person> on the current board.`
- `Use $skill-youtrack to create a subtask under <issue-id>, preview it, and then apply it.`
- `Use $skill-youtrack to add a comment to <issue-id> and then show the updated issue.`
- `Use $skill-youtrack to switch to instance <label> and inspect board <board-id>.`

When an agent needs explicit shell commands, the preferred pattern is:

```bash
~/.codex/skills/skill-youtrack/scripts/ytx board my-tasks
~/.codex/skills/skill-youtrack/scripts/ytx issue brief <issue-id>
```

In a project-local install, the equivalent paths are:

```bash
<repo>/.agents/skills/skill-youtrack/scripts/ytx board my-tasks
<repo>/.agents/skills/skill-youtrack/scripts/ytx issue brief <issue-id>
```

## Update And Maintenance

### Update The Source Skill

If the source skill lives in a versioned directory, update it there first.
Then reinstall or refresh the target environment.

### Refresh A Local Project Copy

After updating the source skill:

```bash
~/agents/skills/skill-youtrack/setup.sh /abs/path/to/repo --locale ru
```

This recopies the source skill into `<repo>/.agents/skills/skill-youtrack`, strips nested
git metadata again, prunes installer-only files again, and refreshes the local runtime.
If that project has already been installed once, you may omit `--locale` and the
stored project locale will be reused.

### Rebuild The Runtime In Place

If the repo already carries the committed runtime copy:

```bash
<repo>/.agents/skills/skill-youtrack/setup.sh
```

### Upgrade The Upstream Dependency Version

Change the pinned version in:

- `scripts/requirements.txt`

Then rebuild the runtime:

```bash
<skill-dir>/scripts/bootstrap.sh --force
```

Because the skill wraps the upstream CLI in-process, any upstream version bump
should be followed by a quick regression pass on:

- `yt auth login`
- `yt instances ...`
- `ytx board ...`
- `ytx issue ...`
- `ytx board create-task ...`
- `ytx board create-subtask ...`

## License

This skill is distributed under the MIT License. See [LICENSE](LICENSE).

The runtime depends on the upstream Python package `youtrack-cli==0.22.2`,
which is also distributed under the MIT License.

Attribution for the upstream CLI:

- project: `yt-cli`
- author: `Ryan Cheley`
- homepage: `https://github.com/ryancheley/yt-cli`
- repository: `https://github.com/ryancheley/yt-cli.git`

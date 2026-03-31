# youtrack-cli

`youtrack-cli` is a standalone skill and local CLI runtime for working with one or more
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
- inspect, search, comment on, and update issues
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
  Install-time translation catalog for user-facing metadata
- `setup.sh`
  Supported installation entrypoint for global or per-repository installs
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

`setup.sh` installs the skill into agent environments.
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

### Global Install

Use this when the skill should be available from your home-level agent setup:

```bash
~/agents/skills/youtrack-cli/setup.sh global --locale en
```

This does the following:

- copies the source skill into a managed runtime directory outside the source repo
- bootstraps the managed copy `.venv/`
- installs the Python dependencies
- renders installed metadata in the requested locale
- links the skill into `~/.claude/skills/youtrack-cli`
- links the skill into `~/.codex/skills/youtrack-cli`

The source of truth remains the source directory:

```text
~/agents/skills/youtrack-cli
```

The managed global install lives under:

```text
${XDG_DATA_HOME:-~/.local/share}/agents/skills/youtrack-cli
```

### Local Install

Use this when a project should carry its own tracked copy of the skill:

```bash
~/agents/skills/youtrack-cli/setup.sh local /abs/path/to/repo --locale ru
```

This does the following:

- copies the skill into `<repo>/.skills/youtrack-cli`
- removes nested git metadata from that copied skill
- bootstraps the copied skill runtime
- renders installed metadata in the selected locale
- links `<repo>/.claude/skills/youtrack-cli` to the local copy
- links `<repo>/.codex/skills/youtrack-cli` to the local copy
- prefixes the local skill metadata with a locale-aware local marker so it is distinguishable in skill UIs

The copied skill is intended to be tracked by the project repository.
Its locale is project-fixed on first install. Later reruns reuse the stored
locale. Passing a different locale for that project copy fails instead of
silently rewriting tracked metadata.

### Locale Selection Rules

- First install requires explicit `--locale`
- Later reruns may omit `--locale` and reuse the stored install manifest value
- Global install may be rerun later with a different locale to re-render the managed copy
- Local install may not change locale after the first project install

## First Login

Authentication requires an explicit instance label:

```bash
~/agents/skills/youtrack-cli/scripts/yt \
  --instance primary \
  auth login \
  --base-url https://your-youtrack-host
```

This stores credentials under the selected label and auto-pins that label for
the current installed copy of the skill.

On large YouTrack instances, strongly prefer setting scoped boards at login:

```bash
~/agents/skills/youtrack-cli/scripts/yt \
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
~/agents/skills/youtrack-cli/scripts/yt \
  --instance primary \
  auth login \
  --base-url https://your-youtrack-host \
  --cert-file /path/to/cert.pem

~/agents/skills/youtrack-cli/scripts/yt \
  --instance primary \
  auth login \
  --base-url https://your-youtrack-host \
  --ca-bundle /path/to/ca-bundle.pem

~/agents/skills/youtrack-cli/scripts/yt \
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
~/agents/skills/youtrack-cli/scripts/yt instances list
~/agents/skills/youtrack-cli/scripts/yt instances current
~/agents/skills/youtrack-cli/scripts/yt instances use primary
~/agents/skills/youtrack-cli/scripts/yt instances scope set primary 83-2561 195-1
~/agents/skills/youtrack-cli/scripts/yt instances scope clear primary
~/agents/skills/youtrack-cli/scripts/yt instances rename primary main
~/agents/skills/youtrack-cli/scripts/yt --instance primary auth status
~/agents/skills/youtrack-cli/scripts/yt --instance primary auth logout
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
3. Use `ytx board scoped-issues --mine` for “my current sprint tasks”
4. Use `ytx board list --scoped` if board metadata is needed
5. Fall back to unrestricted `ytx board list` only when there is no scope

## CLI Usage

### Raw Upstream Coverage Through `yt`

Use `yt` when you want the broader upstream command surface:

```bash
~/agents/skills/youtrack-cli/scripts/yt boards list
~/agents/skills/youtrack-cli/scripts/yt projects list
~/agents/skills/youtrack-cli/scripts/yt issues search "state: Open"
```

### Agent-Friendly Reads Through `ytx`

Use `ytx` when output stability matters:

```bash
~/agents/skills/youtrack-cli/scripts/ytx --instance primary board list --scoped
~/agents/skills/youtrack-cli/scripts/ytx --instance primary board show <board-id>
~/agents/skills/youtrack-cli/scripts/ytx --instance primary board sprints <board-id> --current
~/agents/skills/youtrack-cli/scripts/ytx --instance primary board issues <board-id> --source web
~/agents/skills/youtrack-cli/scripts/ytx --instance primary board scoped-issues --mine

~/agents/skills/youtrack-cli/scripts/ytx --instance primary issue show <issue-id>
~/agents/skills/youtrack-cli/scripts/ytx --instance primary issue search "id: <issue-id>"
~/agents/skills/youtrack-cli/scripts/ytx --instance primary issue comment-list <issue-id>
```

### Current Developer Resolution

`--mine` resolves the current developer from `git config user.email`:

```bash
~/agents/skills/youtrack-cli/scripts/ytx board issues <board-id> --mine
~/agents/skills/youtrack-cli/scripts/ytx board scoped-issues --mine
```

This is intended for boards that use assignee-based workflows. On large
instances, prefer the scoped variant so the agent never starts from a full
board crawl.

### Issue Mutations

```bash
~/agents/skills/youtrack-cli/scripts/ytx issue comment-add <issue-id> "Comment text"
~/agents/skills/youtrack-cli/scripts/ytx issue update <issue-id> --state "In Progress"
~/agents/skills/youtrack-cli/scripts/ytx issue update <issue-id> --custom-field "Field Name=Value"
~/agents/skills/youtrack-cli/scripts/ytx issue command <issue-id> "add tag important"
```

Use `--dry-run` before risky command-based mutations:

```bash
~/agents/skills/youtrack-cli/scripts/ytx issue command <issue-id> "add Board <board-name> <sprint-name>" --dry-run
```

### Explicit Board Membership

For boards that require manual sprint membership:

```bash
~/agents/skills/youtrack-cli/scripts/ytx issue board-add <issue-id> --board "<board-name>" --current-sprint
~/agents/skills/youtrack-cli/scripts/ytx issue board-remove <issue-id> --board "<board-name>" --sprint "<sprint-name>"
```

## Agent Environment Usage

The skill is intended to be called through the installed skill link in Codex or Claude.
Typical agent prompts should ask for the operation, not for the raw shell syntax.

Examples:

- `Use $youtrack-cli to show the current sprint for board <board-id>.`
- `Use $youtrack-cli to list my visible issues on the scoped boards for the current sprint.`
- `Use $youtrack-cli to add a comment to <issue-id> and then show the updated issue.`
- `Use $youtrack-cli to switch to instance <label> and inspect board <board-id>.`

When an agent needs explicit shell commands, the preferred pattern is:

```bash
~/.codex/skills/youtrack-cli/scripts/ytx board scoped-issues --mine
~/.codex/skills/youtrack-cli/scripts/ytx issue show <issue-id>
```

In a project-local install, the equivalent paths are:

```bash
<repo>/.codex/skills/youtrack-cli/scripts/ytx board scoped-issues --mine
<repo>/.codex/skills/youtrack-cli/scripts/ytx issue show <issue-id>
```

## Update And Maintenance

### Update The Source Skill

If the source skill lives in a versioned directory, update it there first.
Then reinstall or refresh the target environment.

### Refresh A Global Install

After updating the source skill:

```bash
~/agents/skills/youtrack-cli/setup.sh global --locale en
```

If the install already has a manifest, you may omit `--locale` to reuse it.
This refreshes the managed runtime copy and reaffirms the symlinks.

### Refresh A Local Project Copy

After updating the source skill:

```bash
~/agents/skills/youtrack-cli/setup.sh local /abs/path/to/repo --locale ru
```

This recopies the source skill into `<repo>/.skills/youtrack-cli`, strips nested
git metadata again, refreshes the local runtime, and keeps the project-local links aligned.
If that project has already been installed once, you may omit `--locale` and the
stored project locale will be reused.

### Rebuild The Runtime In Place

If you only need to refresh the Python environment of an installed copy:

```bash
<skill-dir>/scripts/bootstrap.sh --force
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

## License

This skill is distributed under the MIT License. See [LICENSE](LICENSE).

The runtime depends on the upstream Python package `youtrack-cli==0.22.2`,
which is also distributed under the MIT License.

Attribution for the upstream CLI:

- project: `yt-cli`
- author: `Ryan Cheley`
- homepage: `https://github.com/ryancheley/yt-cli`
- repository: `https://github.com/ryancheley/yt-cli.git`

---
name: skill-youtrack
description: Use when Codex needs to work with one or more self-hosted or cloud YouTrack instances through local CLI commands instead of MCP, including named instance labels, macOS Keychain auth, scoped boards for large instances, board and sprint reads, issue search, comments, field updates, and explicit board or sprint membership through the bundled yt and ytx wrappers.
triggers:
  - "youtrack"
  - "youtrack issue"
  - "youtrack board"
  - "youtrack sprint"
  - "my tasks in youtrack"
  - "youtrack board tasks"
  - "youtrack issue comments"
  - "update youtrack issue"
  - "create youtrack issue"
---

# skill-youtrack

## Default Mode

- Execute the bundled commands yourself and return the result.
- Do not answer with a shell tutorial when the skill is already installed and authentication exists.
- Show commands only when the user explicitly asks for instructions or when setup/auth is missing.
- Answer in the user's language unless the user explicitly asks for a different language.
- The final answer must be entirely in the user's language.
- Do not mix English into headings, summaries, connective text, or explanatory sentences when the user wrote in Russian.
- Keep original language only for literal field values, workflow states, issue ids, board ids, and raw URLs.
- Do not repeat an identical successful read command. Reuse the first successful result unless the context changed or the first result was incomplete.
- Before sending the final answer, do one completeness check against the original user request. If the answer is not good enough yet, keep using tools instead of finalizing.
- Resolve command paths from this skill file path.
- If the skill file path is `/abs/path/to/SKILL.md`, then:
  - `<yt-command>` is `/abs/path/to/scripts/yt`
  - `<ytx-command>` is `/abs/path/to/scripts/ytx`
- Use those absolute command paths for every command in this skill.
- Do not run `scripts/yt` or `scripts/ytx` as paths relative to the current working directory.
- Do not use a bare `yt` from `PATH`.

## Resolve Context First

Always begin with these two commands in this order:

```bash
<yt-command> instances list
<yt-command> instances current
```

Use the result to choose the instance context:

- If the user names a label and it exists, use `--instance <label>` for this turn.
- If `instances current` already points at the correct label, prefer commands without `--instance`.
- If several labels exist and there is no active instance, ask which label to use.
- If no labels exist, stop and ask the user to log in first.

In all commands below:

- `<label>` is a real label from `<yt-command> instances list`
- `<board-id>` is a real agile board id
- `<issue-id>` is a real issue id

## Fast Path: My Tasks

For prompts like:

- "какие мои задачи"
- "мои задачи на доске"
- "what are my tasks this sprint"

use this order:

1. Run `<yt-command> instances list`
2. Run `<yt-command> instances current`
3. Prefer the high-level board read first:

```bash
<ytx-command> board my-tasks
```

4. If the user named a specific board, prefer:

```bash
<ytx-command> board my-tasks --board <board-id>
```

5. Only if the high-level command cannot identify the board context yet, inspect the board context directly:

```bash
<ytx-command> board current
<ytx-command> board list --scoped
```

Return the actual tasks. Do not ask the user to run these commands unless the skill is not installed or auth is missing.

For direct read requests whose target is exactly the current developer's tasks on the current board or sprint, a successful `<ytx-command> board my-tasks` result with `issue_count` and `issues` is sufficient to answer immediately.

Do not run follow-up board discovery commands such as `<ytx-command> board current`, `<ytx-command> board list --scoped`, or a repeated `<ytx-command> board my-tasks --board ...` after a successful `<ytx-command> board my-tasks` unless the original user request still requires information that is not present in that payload.

For compound workflows, `<ytx-command> board my-tasks` may be an intermediate step, but continue only when the next step is required by the original user request.

If the selected instance has exactly one scoped board and the user refers to "the board" loosely, treat that single scoped board as the target board.

## Scope Rules For Large Instances

- If `scoped_board_ids` is configured for the selected instance, treat that scope as the default search surface.
- Do not start with unrestricted `<ytx-command> board list` on large instances.
- Prefer `<ytx-command> board current`, `<ytx-command> board my-tasks`, and `<ytx-command> board tasks` before lower-level reads.
- Prefer `<ytx-command> board list --scoped` before any full-instance board enumeration.

## Board Reads

Use `<ytx-command>` for agent-facing reads:

```bash
<ytx-command> board current
<ytx-command> board my-tasks
<ytx-command> board tasks --assignee <user>
<ytx-command> board tasks --initiator <user>
<ytx-command> board list --scoped
<ytx-command> board show <board-id>
<ytx-command> board sprints <board-id> --current
<ytx-command> board issues <board-id> --source web
<ytx-command> board scoped-issues --mine
```

Rules:

- Prefer `--source web` when the user asks what is visible on the board.
- Use the strict sprint endpoint only when the user explicitly asks about sprint membership.
- Use `<ytx-command> board my-tasks` as the default direct answer for the current developer.
- Use `<ytx-command> board tasks --assignee ...` when the user asks for tasks assigned to someone else.
- Use `<ytx-command> board tasks --initiator ...` when the user asks who initiated the tasks.
- `assignee` and `initiator` are different filters. Do not treat them as interchangeable.
- Use `<ytx-command> board issues ...` and `<ytx-command> board scoped-issues ...` as lower-level inspection tools, not as the default final read path.
- Agent-facing board and task list reads must prefer compact issue payloads. Do not include full issue descriptions or full custom field maps in task-list answers unless the user explicitly asks for issue details.

## Issue Reads

```bash
<ytx-command> issue brief <issue-id>
<ytx-command> issue show <issue-id>
<ytx-command> issue search 'id: <issue-id>'
<ytx-command> issue comment-list <issue-id>
```

## Mutations

Use the high-level `ytx` write surface for board workflows:

```bash
<ytx-command> board create-task --summary 'Task summary'
<ytx-command> board create-subtask --parent <issue-id> --summary 'Task summary'
<ytx-command> issue create --project <project-id> --summary 'Task summary'
<ytx-command> issue create-subtask --parent <issue-id> --summary 'Task summary'
<ytx-command> issue link --source <issue-id> --target <issue-id> --type 'Subtask'
<ytx-command> issue update <issue-id> --state 'In Progress'
<ytx-command> issue update <issue-id> --custom-field 'Field Name=Value'
<ytx-command> issue comment-add <issue-id> 'Comment text'
<ytx-command> issue board-add <issue-id> --board '<board-name>' --current-sprint
<ytx-command> issue board-remove <issue-id> --board '<board-name>' --sprint '<sprint-name>'
<ytx-command> issue command <issue-id> 'add tag important' --dry-run
```

Rules:

- For board workflows, prefer `ytx` over upstream `yt issues create`.
- Do not fall back to `jq`, `grep`, `python -c`, raw keychain reads, or ad-hoc REST calls if the `ytx` surface can do the job.
- New high-level non-destructive writes are preview-first:
  - run the command without `--apply`
  - inspect the preview
  - if it matches the user's request, run the same command again with `--apply`
  - do both steps yourself in the same turn
- Do not ask the user to perform the second `--apply` step manually unless the preview revealed ambiguity or risk that needs clarification.
- Prefer `--dry-run` before raw command mutations when the effect is not obvious.
- For board membership on explicit boards, use `board-add` and `board-remove` instead of guessing field updates.
- Destructive operations still require explicit user intent.
- For `issue create`, `issue create-subtask`, `board create-task`, and `board create-subtask`, a successful preview does not prove that the server will accept the payload.
- If `--apply` returns structured `field_type_mismatch` or `field_required`, stop guessing alternate CLI syntax immediately.
- Do not retry the same create intent by switching between `--field`, `--custom-field`, enum ids, `--Stream Core`, or JSON-like strings such as `Stream=["Core"]`.
- If create fails with `field_type_mismatch`, inspect the structured error payload and reuse the exact field/type guidance from `ytx`.
- If create-subtask fails with `field_required`, read the parent issue and use the structured `retry_with_fields` hint from `ytx` for one guided retry only.
- Multi-value fields must be serialized as repeated `--field 'Name=Value'` arguments. Do not serialize multi-value fields as JSON text inside a single CLI value.
- The guided retry may auto-copy only parent-derived classification fields. Do not auto-copy `Type`, `Priority`, `Assignee`, `State`, or `Initiator`.
- If the guided retry still fails, report the `ytx` defect path and stop instead of continuing trial-and-error retries.
- For task creation and subtask creation, `Assignee` and `Initiator` are mandatory planning inputs even if YouTrack itself can accept a more partial payload.
- Fast path: if the user intent clearly means "assign to me", use `Assignee=me` resolution without asking.
- For any assignee or initiator other than `me`, do not guess from display names alone.
- First try to resolve exact usernames from repo context:
  - `AGENTS.md` or equivalent repo-local agent context, if present
  - explicit team/member mappings with human names, nicknames, YouTrack usernames, or GitLab usernames
- If repo context does not provide an exact mapping, use recent git history only as a weak fallback signal, not as authority.
- If exact assignee or initiator usernames still cannot be determined confidently, ask the user before creating the task.
- Do not create a task with an ambiguous assignee or initiator and hope to fix it later.

## Current Developer Resolution

`--mine` resolves the current developer from `git config user.email`, falls back to global git config, takes the localpart before `@`, and searches YouTrack users with that value.

If the match is ambiguous, `ytx` fails with candidate users. In that case, ask the user which account to use.

## Ask Only If Blocked

Ask the user only when one of these is true:

- no YouTrack instances are configured
- the requested label does not exist
- several labels exist and there is no active instance
- no scoped board matches the request and the target board cannot be identified
- a write target is missing or ambiguous

Otherwise, run the commands and return the result.

## Output Contract

When answering a read query, return the result itself, not a command recipe.

For task lists on a board or sprint:

- include board name
- include sprint name when available
- include total matching issue count
- if total matching issue count is 30 or fewer, list every matching issue id, summary, state, and type
- if total matching issue count is greater than 30, list exactly the first 30 issues and explicitly say that the visible answer is truncated
- include the full raw issue URL for every listed issue when the tool payload provides it
- do not omit `Done` issues unless the user explicitly asks for only active or unresolved work
- write the final answer in the user's language
- localize the fixed labels and headers of the answer to the user's language
- do not use English column headers like `Issue`, `Summary`, `State`, `Type`, or `URL` when the user asked in Russian
- before finalizing, compare the number of listed issues in the answer against the tool payload:
  - if `issue_count <= 30`, the answer must contain exactly `issue_count` listed issues
  - if `issue_count > 30`, the answer must contain exactly 30 listed issues and an explicit truncation note

For a single issue:

- include id, summary, state, type, priority, assignee, and the main description
- prefer `<ytx-command> issue brief <issue-id>` unless the user explicitly asks for the full raw issue
- include a dedicated `Link:` line near the top when the tool payload provides a full issue URL
- localize fixed labels like `Link:` to the user's language

## Pre-final Check

Before sending the final answer:

1. Draft the answer from the tool results you already have.
2. Compare that draft against the original user request.
3. Ask these questions:
   - Does the draft answer the user's actual question directly?
   - If the user named a specific instance, board, sprint, or issue, does the answer refer to that exact target?
   - Does the answer satisfy the output contract for this query type?
   - Is the answer still telling the user to run commands even though the skill and auth are already available?
   - Is every non-literal part of the final answer written in the user's language?
4. If any answer is "no", continue with the next needed tool call instead of finalizing.
5. If all answers are "yes", send that draft as the final answer.

Do this once per response. Do not loop on repeated self-checks after you already have a good direct answer.

## Setup Fallback

Use this only when installation or authentication is missing.

Install globally:

```bash
setup.sh global --locale <locale>
```

Install into one repository:

```bash
setup.sh local /abs/path/to/repo --locale <locale>
```

Log in with an explicit label and optional scoped boards:

```bash
<yt-command> \
  --instance <label> \
  --board-id <board-id> \
  --board-id <board-id> \
  auth login \
  --base-url https://your-youtrack-host
```

Pin the active instance:

```bash
<yt-command> instances use <label>
```

## Safety Rules

- Keep tokens in macOS Keychain through `<yt-command> auth login`.
- Do not save tokens in shell history, env files, or checked-in files.
- Use an explicit label for each instance.
- On large instances, keep agents inside scoped boards by default.
- Prefer `<ytx-command>` for reads consumed by agents because it emits stable JSON.

## References

- [`references/board-membership.md`](references/board-membership.md) for explicit/manual board behavior and `web` vs `strict` reads

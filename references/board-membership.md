# Board Membership Notes

Use this reference when a YouTrack agile board looks inconsistent between the web UI and REST API.

## Two Different Views Of A Sprint

`ytx board issues` supports two sources:

- `--source web`
- `--source strict`

`web` reads the sprint object itself with `issues(...)` expanded. This better matches what the board UI currently shows, including manually added or orphan cards on explicit boards.

`strict` reads `/api/agiles/{board}/sprints/{sprint}/issues`. This is narrower and can omit cards that are visible on the board UI.

When the user asks "what is on the board", prefer `web`.

When the user asks "what is in the strict sprint membership endpoint", use `strict`.

## Explicit Sprint Boards

Some boards are configured with:

- `sprintsSettings.isExplicit = true`
- `sprintSyncField = null`

In that mode:

- issue links do not automatically add a card to the board
- a custom field like `Спринт` does not control board membership
- issues must be explicitly added or removed from the board/sprint

Use:

```bash
scripts/ytx issue board-add ISSUE-ID --board 'Board Name' --current-sprint
scripts/ytx issue board-remove ISSUE-ID --board 'Board Name' --sprint 'Sprint Name'
```

Under the hood this applies a YouTrack command like:

```text
add Board Board Name Sprint Name
remove Board Board Name Sprint Name
```

## `--mine` Resolution

`ytx board issues --mine` resolves the current developer by:

1. reading `git config user.email` in the current repo
2. falling back to `git config --global user.email`
3. taking the localpart before `@`
4. searching YouTrack users with that value
5. preferring an exact `login`, then exact email localpart, then a `login` with that localpart as prefix

If the result is ambiguous, `ytx` fails and prints candidates instead of guessing.

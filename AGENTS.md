# AGENTS.md: lb-mapper

lb-mapper maps unlinked ListenBrainz listens to MusicBrainz recordings through the `map-listens` skill in `.agents/skills`.

## Responsibilities

The shared `map-listens` skill owns matching judgment, research, final review, and authorization. Python owns API calls, grouping, validation, and execution records. Each API module owns its HTTP client. Keep artist aliases and musical identity reasoning in the skill.

A recording MSID can occur many times. A mapping applies to that MSID, while deletion targets an individual `(listened_at, recording_msid)` occurrence. Preserve submitted metadata when linking.

## Contracts

- CLI snapshots use JSON and search / execution records use JSONL. Progress goes to stderr.
- Fail on malformed external responses. Search outages must remain distinguishable from a successful search with no results.
- `execute` validates the complete action batch before any request, and `--apply` checks that the configured user owns the token before mutating the account.
- API acceptance of a deletion means it was scheduled. Confirm absence before reporting it as deleted.
- Runtime snapshots and review evidence belong in the ignored `runs/` directory. Never write tokens to these artifacts.

## Development

Account jobs share the main checkout's `.venv`, so never sync or change dependencies there while one may be running. Verify changes from a separate worktree with:

```bash
nix develop -c run-hooks
nix develop -c uv run --locked pytest
```

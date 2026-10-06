# AGENTS.md: lb-mapper

Project-specific instructions for any coding assistant working in this repository. `CLAUDE.md` links to this file, and `.claude/skills` links to `.agents/skills`.

## Responsibilities

The shared `map-listens` skill owns matching judgment, research, final review, and authorization. Python owns API calls, grouping, validation, and execution records. Keep artist aliases and musical identity reasoning in the skill.

- `lb_client.py` owns authenticated ListenBrainz operations and rate limits.
- `lb_search.py` owns LB Labs search and bulk recording lookup.
- `mb_search.py` owns MusicBrainz search and recording lookup. A single process must own MusicBrainz calls during a run to respect its shared rate limit.
- `history.py` reads enriched history exports newest first.
- `review.py` groups occurrences by MSID and requires complete decisions before preparing actions.
- `cli/` communicates using JSON snapshots and JSONL search / execution records. Progress goes to stderr.

A recording MSID can occur many times. A mapping applies to that MSID, while deletion targets an individual `(listened_at, recording_msid)` occurrence. Preserve submitted metadata when linking.

## Contracts

- Fail on malformed external responses. Search outages must remain distinguishable from a successful search with no results.
- Validate the complete action batch before making requests. Check the configured user against the token owner before mutations.
- API acceptance of a deletion means it was scheduled. Confirm absence before reporting it as deleted.
- Runtime snapshots and review evidence belong in the ignored `runs/` directory. Never write tokens to these artifacts.
- `.env` is private. Check or consume credentials without printing their values.

## Development

Use Python 3.12+, `uv`, single quotes, and the existing Ruff configuration. Keep runtime dependencies limited to `httpx` and `python-dotenv`. Each API module owns its HTTP client.

Run relevant behavioral tests when changing the API or execution contracts, then check:

```bash
uv run python -m unittest discover -s tests
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/lb_mapper/ --strict
uv run pre-commit run --all-files
```

Commits use `type(scope): description`, with an optional scope. Load the shared Git Workflow skill for commits and publication.

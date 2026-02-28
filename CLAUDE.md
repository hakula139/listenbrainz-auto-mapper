# CLAUDE.md — lb-mapper

## Project Overview

lb-mapper automatically maps unlinked ListenBrainz listens to MusicBrainz recordings. The matching intelligence lives in a Claude Code skill (`/map-listens`); the Python code provides thin API wrappers.

### Project Layout

```text
src/lb_mapper/                    Python package
  __init__.py                     Package docstring
  lb_client.py                    ListenBrainz API client (fetch, map, delete)
  lb_search.py                    LB Labs Typesense search + CJK detection
  cli/                            CLI helpers invoked by the skill
    fetch_listens.py              Fetch unlinked listens → JSON
    search_batch.py               Batch search LB Labs → JSON
    execute.py                    Submit mappings + delete listens

.claude/skills/map-listens/       Claude Code skill
  SKILL.md                        Skill definition (orchestration + domain rules)
```

### Key APIs

- **ListenBrainz API** (`api.listenbrainz.org`): authenticated; fetch listens, submit mappings, delete listens. Rate-limited via `X-RateLimit-*` headers.
- **LB Labs API** (`labs.api.listenbrainz.org`): unauthenticated Typesense search. Better fuzzy matching than MusicBrainz Lucene, especially for classical titles and CJK text.

## Coding Conventions

### Style

- Formatter / linter: `ruff` (88-char lines, config in `pyproject.toml`)
- Type checker: `mypy --strict`
- Quote style: single quotes
- Lint rules: `B`, `C4`, `E`, `F`, `I`, `SIM`, `UP`, `W`

### Dependencies

- Runtime: `httpx`, `python-dotenv`. Keep it minimal.
- Dev tools (`ruff`, `mypy`, `pre-commit`) are in `[dependency-groups] dev`.

### Error Handling

- Fail fast on structural API contract violations (e.g., missing `track_metadata`).
- Use `.get()` with defaults for optional / nullable fields.
- Search functions return empty lists on HTTP errors (best-effort, non-critical).
- The `ListenBrainzClient` sleeps proactively when rate-limit headroom is low.

### Module Boundaries

- `lb_client.py` owns all authenticated ListenBrainz operations.
- `lb_search.py` owns LB Labs search and CJK text detection.
- Each module creates its own `httpx.Client` — no shared factory.
- `lb_search.py` uses lazy initialization (`@cache`) since it is a module-level singleton.

### CLI Helpers

The `lb_mapper.cli` subpackage contains scripts invoked by the skill via `uv run python -m lb_mapper.cli.<script>`. They:

- Import from the `lb_mapper` package (installed by `uv sync`)
- Use `python-dotenv` (`load_dotenv()`) to load `.env`
- Communicate via JSON on stdin / stdout; progress on stderr

## Git Conventions

- Commit messages: `type: description`
- Types: `feat`, `fix`, `refactor`, `docs`, `chore`
- Keep commits atomic — one logical change per commit.

## Verification

Pre-commit hooks run automatically on `git commit` (mypy, ruff, bandit, trailing whitespace, etc.). To run manually:

```bash
uv run pre-commit run --all-files
```

Individual tools:

```bash
uv run ruff check src/
uv run ruff format --check src/
uv run mypy src/lb_mapper/ --strict
```

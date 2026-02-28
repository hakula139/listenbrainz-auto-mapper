# lb-mapper

Automatically map unlinked [ListenBrainz](https://listenbrainz.org/) listens to [MusicBrainz](https://musicbrainz.org/) recordings.

## Why

ListenBrainz listens submitted by third-party scrobblers (Apple Music, Spotify, etc.) often lack MusicBrainz recording IDs, leaving them "unlinked". Linking them manually through the web UI is tedious. This tool automates the process: it searches for matches, uses LLM reasoning to evaluate correctness (including tricky classical music and CJK artist names), and submits approved mappings.

## How It Works

The matching intelligence lives in a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill (`/map-listens`), not in hardcoded heuristics. The Python code provides thin API wrappers; the LLM handles evaluation.

**Pipeline:**

1. Fetch recent listens and filter to unlinked ones
2. Translate CJK artist names to English (with a local cache)
3. Search [LB Labs](https://labs.api.listenbrainz.org/) (Typesense) for recording matches
4. LLM evaluates each match using domain rules (classical catalog numbers, artist disambiguation, arrangement annotations, etc.)
5. Present results for user approval
6. Submit approved mappings / delete bad listens

See [`.claude/skills/map-listens/SKILL.md`](.claude/skills/map-listens/SKILL.md) for the full procedure.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- A [ListenBrainz API token](https://listenbrainz.org/settings/)

## Setup

```bash
git clone https://github.com/hakula/listenbrainz-auto-mapper.git
cd listenbrainz-auto-mapper
uv sync
cp .env.example .env
# Edit .env and fill in your ListenBrainz username and token
```

## Usage

Inside Claude Code, from the repo root:

```text
/map-listens        # process the last 1000 listens
/map-listens 500    # process the last 500 listens
```

## Development

```bash
uv sync --group dev
uv run pre-commit install
uv run pre-commit run --all-files
```

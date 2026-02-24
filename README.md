# lb-mapper

Automatically map unlinked [ListenBrainz](https://listenbrainz.org/) listens to [MusicBrainz](https://musicbrainz.org/) recordings.

## Why

ListenBrainz listens submitted by third-party scrobblers (Apple Music, Spotify, etc.) often lack MusicBrainz recording IDs, leaving them "unlinked". Linking them manually through the web UI is tedious. This tool automates the process by searching MusicBrainz for each unlinked listen and submitting the best match.

For CJK artist names (Japanese katakana transliterations, Chinese names, etc.), the tool uses an LLM to translate them to English before searching, significantly improving match rates.

## Features

- **Multi-strategy matching** -- exact search, normalized track name, track + release fallback, track-only fallback
- **Track name normalization** -- strips TV edits, classical movement / tempo markings, key signatures, and `feat.` credits
- **CJK artist translation** -- translates Japanese / Chinese / Korean artist names via [Codex](https://openai.com/index/codex/) MCP with a local cache
- **Dry-run mode** -- preview matches without submitting
- **Automatic SSL handling** -- detects local MITM proxies and skips certificate verification

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [Codex CLI](https://github.com/openai/codex) (for CJK translation)
- A [ListenBrainz API token](https://listenbrainz.org/settings/)

## Installation

```bash
git clone https://github.com/hakula/listenbrainz-auto-mapper.git
cd listenbrainz-auto-mapper
uv sync
```

## Configuration

Copy the example env file and fill in your token:

```bash
cp .env.example .env
```

```env
LB_TOKEN=your-listenbrainz-token-here
```

## Usage

```bash
# Map the 50 most recent listens (dry run)
lb-mapper map --user YOUR_USERNAME --dry-run

# Map and submit
lb-mapper map --user YOUR_USERNAME

# Process more listens
lb-mapper map --user YOUR_USERNAME --count 200

# Paginate: only process listens before a Unix timestamp
lb-mapper map --user YOUR_USERNAME --max-ts 1700000000
```

### Output

```text
  ✓ ヨルシカ → Yorushika — Say It. (score: 100)
    → Yorushika — Say It. (score: 100)
  ✗ Some Artist — Obscure Track
  ✓ Beethoven — Piano Sonata No. 14 (score: 95)
    → Ludwig van Beethoven — Piano Sonata No. 14 (score: 95)

Done: 42 mapped, 3 unmatched, 5 already linked.
```

## How It Works

1. Fetch recent listens from the ListenBrainz API
2. Skip already-linked listens
3. Search MusicBrainz with original artist + track name
4. If the artist name contains CJK characters, translate via Codex and retry
5. Fall back to track + release name search, then track-only search
6. Submit the best match (score >= 90) as a manual MBID mapping

## Development

```bash
uv sync --group dev
ruff check src/
ruff format src/
mypy src/lb_mapper/ --strict
```

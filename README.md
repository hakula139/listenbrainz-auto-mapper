# lb-mapper

Map unlinked [ListenBrainz](https://listenbrainz.org/) listens to [MusicBrainz](https://musicbrainz.org/) recordings and clean up listens that remain unmatched after research and final review.

## How it works

The shared [map-listens skill](.agents/skills/map-listens/SKILL.md) runs in Codex or Claude Code. Python handles API operations and records, while the coordinating assistant researches recording identity and reviews the decisions.

1. Collect the requested number of recent unlinked listens, preserving source metadata.
2. Group repeated recording MSIDs and gather candidates from LB Labs and MusicBrainz.
3. Recover missing matches with verified aliases, localized titles, and catalog queries.
4. Review every decision. Classical listens can use another performer's recording of the same work and movement. Unresolved listens are deletion candidates after the search is complete.
5. Apply actions within the user's authorization and verify the results. Linking preserves submitted metadata. Deletions are scheduled by ListenBrainz and need a later absence check.

Large history scans use the official ListenBrainz export API. Snapshots, candidate searches, review evidence, and execution results are saved under the ignored `runs/` directory. Successful searches can be resumed without repeating requests. Native subagents can research independent groups, and ACP is optional for consulting another assistant.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Codex or Claude Code
- A [ListenBrainz API token](https://listenbrainz.org/settings/)

## Setup

```bash
git clone https://github.com/hakula139/listenbrainz-auto-mapper.git
cd listenbrainz-auto-mapper
uv sync
cp .env.example .env
# Set LB_USER and LB_TOKEN in .env
```

## Usage

From the repo root, ask Codex to use `$map-listens` and specify the number of unlinked listens. Claude Code exposes the same skill as `/map-listens`:

```text
$map-listens collect and process 5000 unlinked listens
/map-listens 5000
```

The count is the number of listen occurrences, so repeated tracks can yield fewer distinct mapping decisions. The assistant handles final review. State whether it may submit mappings and delete unmatched listens, or whether you want a proposal first.

For direct CLI use, the skill documents the file formats and commands. The execution helper validates a plan by default and requires `--apply` to mutate the account.

## Development

```bash
uv sync --group dev
uv run pre-commit install
uv run python -m unittest discover -s tests
uv run pre-commit run --all-files
```

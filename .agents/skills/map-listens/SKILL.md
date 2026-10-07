---
name: map-listens
description: Research, link, and clean up unlinked ListenBrainz listens using recording evidence and a final coordinating-agent review. Use for requested ListenBrainz mapping or unmatched-listen cleanup.
---

# Map Listens

Collect and process recent unlinked listen occurrences for `LB_USER`, using `LB_TOKEN` from `.env`. Count defaults to 100. Codex coordinates directly, and Claude Code can use the same procedure.

## Authorization and review

The coordinating agent owns final review. Honor authorization already given for mappings, deletions, and classical substitutions. If the user asks the agent to review and execute, perform that review and execute within that scope. Obtain any missing mutation authorization before applying actions.

The cleanup goal is best-effort matching followed by deletion of genuinely unresolved listens. Search errors and unfinished research remain pending. A successful search with no acceptable candidate can support deletion after independent MusicBrainz recovery and final review. Labs can return an empty result when its search backend fails, so a Labs miss alone never establishes absence. Script or language alone does not establish that a listen is unmatchable.

For classical music, another performer's recording is acceptable when it represents the same work, catalog number, key, movement, and arrangement. Preserve instrumentation and named arrangements when substituting performers. Label it `substitute` so the changed performer is visible in the review record. Linking changes the MSID mapping and preserves the submitted title and metadata.

## Run artifacts and commands

Initialize the locked environment once with `uv sync --locked`, then run Python with `uv run --no-sync` from the repo root. Parallel workers must share that initialized environment without syncing dependencies during active jobs. Use an isolated `UV_PROJECT_ENVIRONMENT` for dependency edits and verification while account jobs run. Create an ignored `runs/<run-name>/` directory. Keep the snapshot, query results, evidence, decisions, action plan, and execution / verification records there.

```bash
run=runs/<run-name>
mkdir -p "$run"
uv run --no-sync python -m lb_mapper.cli.fetch_listens COUNT --output "$run/listens.json"
uv run --no-sync python -m lb_mapper.cli.lookup_batch --input "$run/listens.json" --output "$run/exact.jsonl"
```

Keep ListenBrainz API and Labs requests in one process per run, including health probes and verification. The clients pause for three seconds before each request. During service instability, increase the spacing for the run and avoid concurrent retry loops. Research agents can work from saved artifacts while account operations remain serial.

For large or sparse histories, use an enriched history export. ListenBrainz recommends the export API for complete history and the listens endpoint for recent listens, bounded queries, and incremental updates. An export job can be resumed using its saved state, and exported months are processed newest first:

```bash
uv run --no-sync python -m lb_mapper.cli.export_history --output "$run/history.zip"
uv run --no-sync python -m lb_mapper.cli.fetch_listens COUNT --export "$run/history.zip" --output "$run/listens.json"
```

The snapshot contains `{user, requested, total, linked, unlinked}`. Each occurrence retains `listened_at`, `recording_msid`, `artist`, `track`, `release`, `additional_info`, and `mbid_mapping`. The search helper groups occurrences by MSID, preserving all occurrences under `listens`. Review conflicting metadata within a group before selecting one mapping.

The bulk lookup submits up to 100 original artist / title pairs per request. Its canonical normalized candidate still needs identity review. Request echoes and indices establish input routing, while artist, title, version, and release fields establish musical identity. Different raw pairs can collide under server normalization, so a missing row remains unresolved. Do not propagate candidates across merely normalized pairs.

Review lookup candidates, then write unresolved groups to `unresolved.json` as an array of objects or a filtered snapshot and run fuzzy search:

```bash
uv run --no-sync python -m lb_mapper.cli.search_batch --input "$run/unresolved.json" --output "$run/labs.jsonl"
```

Search output is JSONL. Each record includes the input, `source`, `query`, and `status`. Successful records contain `results`, while failed records contain `error`. Reusing the output file resumes successful queries and retries failed ones. Never interpret `status: error` as no match.

For recovery queries, write an array of objects to a JSON file. Each object identifies its `recording_msid` and either `artist` / `track` or an explicit MusicBrainz Lucene `query`:

```bash
uv run --no-sync python -m lb_mapper.cli.search_batch --source musicbrainz --input "$run/queries.json" --output "$run/musicbrainz.jsonl"
```

An object with `recording_mbid` performs a MusicBrainz lookup with artist credits, releases, ISRCs, and work relationships. Copy target MBIDs from returned records and confirm every selected target with a successful canonical recording lookup before preparing actions. Preparation checks the selected MSID / target pairs against this MusicBrainz JSONL journal. One process owns MusicBrainz requests during a run. The client spaces requests by 1.1 seconds and resolves merged recording identifiers to their surviving ID. Use supported recording-index fields such as `recording`, `artist`, `artistname`, `release`, `isrc`, and `comment`. The recording index has no `composer` or `work` field. Search catalog and movement tokens in `recording`, then inspect work relationships to establish composer identity. An unsupported field returning zero results does not support deletion. See [MusicBrainz search fields](https://musicbrainz.org/doc/MusicBrainz_API/Search).

Search records retain `result_count`, `result_offset`, and `next_offset` when further results exist. Narrow a broad query or request another page using its `offset` before concluding that no acceptable candidate exists.

## Finding an acceptable match

Search original metadata first. Treat fuzzy hits as candidates whose identity still needs checking. Search results and index scores do not establish a match.

- Check titles, artist identity, release context, duration, ISRC, and source URLs when available. Use per-track durations, since album pages can show totals for grouped works. Multiple releases can share one recording. A recording may credit the composer while its release credits the performers, so inspect both before excluding it.
- Preserve version distinctions: live, studio, instrumental, remix, arrangement, TV edit, and extended versions can be different recordings. Remastering alone usually retains the recording identity.
- Classical catalog tags, work numbers, keys, and movements must agree. Movement titles can abbreviate internal tempo changes. Compare source track segmentation and score structure before treating omitted title words as omitted music. A composition match permits performer substitution under this project's policy. Identify the substituted performance explicitly.
- Generic titles and ambiguous romanizations need corroboration. Given names, similar spellings, and kanji homophones do not independently establish artist or title identity.

For unresolved items, use the recovery angles justified by their metadata:

- Verified native-script names, canonical romanizations, and localized titles in both directions. Preserve catalog information and mixed-script title segments.
- Base titles without storefront album, film, or feature-credit suffixes. Keep version distinctions in the identity review even when simplifying the search query.
- Simplified multi-artist credits and featured-artist variants. Verify the full credit after retrieval.
- Direct MusicBrainz queries with distinctive title tokens, release context, ISRC, or classical catalog / movement identifiers. Broaden classical searches to other performers before declaring failure.
- Web research on MusicBrainz, the source album page, artist discography, or publisher catalog to resolve naming and work identity.

Record verified aliases with their context and source in the run evidence. Do not infer one-to-one title translations from a flat bidirectional cache.

Use GPT-6 Luna (`gpt-6-luna`) for mapping-check agents. The coordinating agent reviews their evidence and owns final decisions. If that model is unavailable, report the limitation before choosing another model.

For large runs, native subagents can research disjoint MSID groups and propose decisions with evidence. Give each agent only its relevant snapshot and candidates, plus this skill. Keep agents read-only against ListenBrainz and give their output files distinct owners. Use the shared ACP Delegation skill only when consulting another configured assistant materially helps. Before proposing deletion, check a distinctive title without the original artist restriction, or use a verified alternate credit with release / catalog context when the title is generic. For romanized or translated source metadata, inspect the native source track listing and search its exact native title before concluding absence. Broaden localized-release searches to verified romanized titles, credited composers, ensembles, and source-album names. A second query constrained to the same original artist and translated release does not establish that recovery is complete. Review any partial or truncated search results before concluding absence.

Every proposed deletion and classical substitution receives the coordinating agent's final review.

## Decisions and execution

Write `decisions.json` as an array with exactly one entry for every snapshot MSID. Each entry contains `recording_msid`, `verdict`, `reason`, and `evidence` pointing to the completed queries and identity sources. Join snapshots, queries, lookups, and decisions by `recording_msid`. Their array positions can differ. Derive source IDs from the matching snapshot object and target IDs from returned recording objects.

- `link`: acceptable recording identity, with `recording_mbid`.
- `substitute`: same classical work / movement / arrangement with another performer, with `recording_mbid` and the substitution rationale.
- `delete`: no acceptable match after recovery and final review, with `search_complete: true`.
- `skip`: pending failed queries or unfinished research. State what remains unresolved.

After a mapping conflict, look up the existing recording ID and review its canonical identity. To replace an existing mapping after that review, include `previous_recording_mbid` in the decision. Execution permits replacement of that observed ID. An absent mapping is created normally, and the new mapping is confirmed through readback. Another existing mapping remains a conflict.

Reconcile all groups against the snapshot. Resolve conflicting choices for one MSID and check that every occurrence is covered. Evaluate the strongest candidates beyond an arbitrary top-five cutoff when later results provide better evidence. Final review must examine the actual candidates and sources, including proposed links, rather than merely accepting another agent's verdict.

```bash
uv run --no-sync python -m lb_mapper.cli.prepare "$run/listens.json" "$run/decisions.json" --recordings "$run/musicbrainz.jsonl" --output "$run/actions.json"
uv run --no-sync python -m lb_mapper.cli.execute --input "$run/actions.json"
uv run --no-sync python -m lb_mapper.cli.execute --input "$run/actions.json" --apply --output "$run/execution.jsonl"
uv run --no-sync python -m lb_mapper.cli.verify --input "$run/actions.json" --output "$run/verification.jsonl"
```

Only the `--apply` command mutates the account. It checks the token owner, rejects conflicting actions, confirms mappings through readback, and rechecks listen / mapping state before scheduling deletions. Re-review conflicts or unexpected state changes. An HTTP failure stops the batch and preserves the failed result. Pause account operations until a bounded health check succeeds, then rebuild the remaining plan from execution and verification records. Exclude confirmed mappings and accepted deletion requests. Read back ambiguous mutations before deciding whether to retry them.

Report mapped MSIDs, affected occurrences, substitutions, pending research, errors, and deletion states separately. `scheduled` means ListenBrainz accepted a deletion request. Deletions usually run shortly after the hour, so immediate readback can remain pending. Report a listen as deleted only after verification returns `absent`. Verification appends timestamped observations. Use the latest observation for each mapping MSID or deletion occurrence when reporting current state, including across resumed journals. Verification exits 2 while deletions remain pending, and 1 on errors or mismatched mappings. See the [ListenBrainz API contract](https://listenbrainz.readthedocs.io/en/latest/users/api/core.html) for history retrieval and deletion timing.

---
name: map-listens
description: Fetch unlinked ListenBrainz listens, search for MusicBrainz matches, evaluate with domain reasoning, and execute mappings / deletions with user approval.
disable-model-invocation: true
---

# Map Listens

Map unlinked ListenBrainz listens to MusicBrainz recordings for the user specified by `LB_USER` in `.env`.

## Invocation

`/map-listens [count]` — count defaults to 100.

## Setup

The project lives at the repo root. The `.env` file contains `LB_TOKEN` and `LB_USER`. Python code runs via `uv run` from the repo root. CLI helpers are in the `lb_mapper.cli` package:

- `lb_mapper.cli.fetch_listens` — fetch recent listens, output unlinked as JSON
- `lb_mapper.cli.search_batch` — batch search LB Labs for recording matches
- `lb_mapper.cli.execute` — submit approved mappings and delete approved listens

The CJK translation cache is at `~/.cache/lb-mapper/translations.json` (JSON object mapping original artist names to English translations).

## Pipeline

### Phase 1: Fetch and Filter

```bash
uv run python -m lb_mapper.cli.fetch_listens COUNT
```

Replace `COUNT` with the number of **unlinked** listens to find (default 100). The script paginates through history until enough unlinked listens are collected. It outputs JSON to stdout with `total` (scanned), `linked`, and `unlinked` fields. Report the totals to the user.

### Phase 2: Translate CJK Artists

Load the translation cache from `~/.cache/lb-mapper/translations.json`. For any unlinked listen whose artist name contains CJK / Hangul / Kana characters (detected by `contains_cjk()` in `lb_mapper.lb_search`):

1. Look up the artist in the cache. If found, use the cached translation.
2. If NOT found in cache, translate the artist name to its English equivalent (it may be a katakana transliteration of a Western name, or a native CJK name). If Codex MCP is available, delegate the translation to it; otherwise, translate directly.
3. Update the cache file after translating new names.

### Phase 3: Search

Use the helper script to batch-search. Pipe a JSON array of objects to stdin:

```bash
echo '$JSON_ARRAY' | uv run python -m lb_mapper.cli.search_batch
```

Each object has fields: `artist` (translated name if CJK, otherwise original), `track`, `release`, and `original_artist` (the raw artist name from the listen — set to empty string if no CJK translation was needed).

The script searches LB Labs (Typesense) with the `artist` field first. If `original_artist` contains CJK and the first search returns no results, it retries with the original CJK name.

The script returns a JSON array where each element contains the original input plus a `results` array of matches (each with `recording_mbid`, `recording_name`, `release_name`, `release_mbid`, `artist_credit_name`, `artist_credit_id`).

Process in batches of ~50 to avoid overwhelming stdout.

### Phase 4: Evaluate Matches (LLM Reasoning)

This is the core intelligence. For each listen with search results, reason about whether the top result is the correct match. Do NOT use hardcoded thresholds. Instead, apply these domain rules:

#### Artist Verification

- The match's `artist_credit_name` must plausibly refer to the same artist(s) as the listen.
- Account for separator differences: `&` vs `,` vs `feat.` vs `and` are equivalent.
- Account for minor spelling variations (e.g., "Capuccelli" vs "Capucelli"). <!-- cspell:disable-line -->
- Watch for **substring false positives**: "Foster" as an artist should NOT match "Neil Foster" or "Kendra Foster" — these are different artists. A short artist name appearing as a substring of a longer, different name is a mismatch.
- CJK artist names may appear directly in MB credits as aliases. Check both the translated name AND the original against the credit.

#### Title Matching — General

- Titles must refer to the **same recording**, not just share some words.
- Short titles (one or two common words like "Alive", "Home", "Love") are inherently ambiguous — require stronger artist + release evidence before accepting.
- Annotations present on only one side — "(TV Edit)", "[Deluxe]", "(Arr. for Piano)", "(feat. X)" — are generally noise. Accept if the underlying work is the same; MB titles often omit what the listen includes.
- Conflicting version indicators on both sides — e.g., listen says "Orchestral Version" but match says "Acoustic" — indicate genuinely different recordings. Reject unless the release context reconciles them.

#### Title Matching — Classical Music

Classical titles encode precise work identity. Two recordings that share a generic title ("Allegro", "Sonata") but differ in any of these identifiers are **different works**:

- **Catalog numbers**: Op. / Opus, K. / KV (Mozart), BWV (Bach), TWV (Telemann), HWV (Handel), RV (Vivaldi), Wq (C.P.E. Bach), D. (Schubert), S. (Liszt), Hob. (Haydn)
- **Work numbers**: No. / Nr. within an opus
- **Key signatures**: "in C Major" vs "in B-flat Minor" — different works entirely
- **Movement markings**: If the listen specifies a movement (e.g., "II. Allegro") and the match is a different movement or the complete work, that is a mismatch

Example rejections:

<!-- cspell:disable -->
- Listen: "Nocturne in E-flat major, Op. 9 No. 2" vs Match: "Nocturne in B-flat minor, Op. 9 No. 1" — same opus, but different nocturne and different key
- Listen: "Violin Concerto in D major, Op. 77: II. Adagio" vs Match: "Violin Concerto in D major, Op. 77: III. Allegro giocoso" — same concerto, wrong movement
- Listen: "Prelude and Fugue in C major, BWV 846" vs Match: "Prelude and Fugue in C minor, BWV 871" — both preludes and fugues in C, but different BWV (WTC Book I vs Book II)
<!-- cspell:enable -->

Example acceptance:

<!-- cspell:disable -->
- Listen: "Lakme, Act 1: Duo des fleurs (Transcr. Ducros for Cello Ensemble) [Classical Session]" vs Match: "Lakme: Act 1: Duo des fleurs" — same work, arrangement annotation is noise
- Listen: "Mozart: Piano Sonata K. 331: III. Rondo alla Turca" vs Match: "Piano Sonata no. 11 in A major, KV 331: III. Alla turca" — same piece; K. and KV are equivalent catalog abbreviations, "Rondo alla Turca" is the popular name for the "Alla turca" movement
- Listen: "Beethoven: Moonlight Sonata: I. Adagio sostenuto" vs Match: "Piano Sonata no. 14 in C-sharp minor, op. 27 no. 2: I. Adagio sostenuto" — "Moonlight Sonata" is the nickname for Op. 27 No. 2
- Listen: "Schubert: Die Forelle, D. 550" vs Match: "The Trout, op. 32, D 550" — same lied, German vs English title, matching Deutsch catalog number confirms identity
<!-- cspell:enable -->

#### CJK / Katakana Handling

<!-- cspell:disable -->
- Katakana artist names with zero usable search results across all search strategies should be flagged for **deletion** — they are likely bad scrobbles from Japanese streaming services that will never match. "Katakana artist" means the name is composed of katakana (transliterating a Western name), even if it includes standard Japanese ensemble / instrument suffixes in kanji such as 四重奏団 (quartet), 管弦楽団 (orchestra), 室内管弦楽団 (chamber orchestra), 交響楽団 (symphony orchestra), or 合唱団 (choir). These suffixes do not make the artist "mixed-script" — the core name is still a transliteration.
- Mixed scripts (katakana + Latin, e.g., "キャロル&チューズデイ(Vo.Nai Br.XX&Celeina Ann)") should NOT be auto-deleted; these often have legitimate MB entries. Likewise, artists with genuine kanji names (e.g., 辻井伸行, 角野隼斗) are native Japanese artists and should NOT be auto-deleted.
<!-- cspell:enable -->

#### Verdict Categories

Classify each listen into one of:

1. **link** — Confident match. Same work, compatible artist, title clearly identifies the same recording.
2. **review** — Uncertain. Plausible but ambiguous (short title, only partial title overlap, arrangement differences, artist credit includes the expected name but is a larger ensemble).
3. **skip** — No usable match found. Leave for future processing.
4. **delete** — Bad listen that will never match (katakana-only artist with no results, garbled metadata).

### Phase 5: Present Results for Approval

Present results in a structured batch for user review. Group by verdict:

**Links (N items)** — show each as:

```text
[artist] — [track]
  -> [match_artist] — [match_title] ([recording_mbid])
```

**Reviews (N items)** — show each with a brief note on why it is uncertain:

```text
[artist] — [track]
  -> [match_artist] — [match_title] ([recording_mbid])
  Note: [reason for uncertainty]
```

**Deletions (N items)** — show each with reason:

```text
[artist] — [track] (listened_at: [timestamp])
  Reason: [why this should be deleted]
```

**Skips (N items)** — just list them briefly.

Wait for user confirmation before executing anything. The user may:

- Approve all
- Approve links only
- Cherry-pick specific items
- Override verdicts (move a "review" to "link" or "skip")
- Ask for re-evaluation of specific items

### Phase 6: Execute

After user approval, build a JSON object with `mappings` and `deletions` arrays, then pipe it to the execute script:

```bash
echo '{"mappings": [...], "deletions": [...]}' | \
    uv run python -m lb_mapper.cli.execute
```

Each mapping entry needs `recording_msid` and `recording_mbid`. Each deletion entry needs `listened_at` and `recording_msid`. The script handles rate limits internally and reports each action as it completes.

## Parallelism

When evaluating a large batch (50+ items), parallelize evaluation. Split the batch into chunks and evaluate each chunk concurrently — use Codex MCP sessions if available, otherwise use Claude Code subagents. Collect results and merge before presenting.

## Important Notes

- NEVER submit a mapping or delete a listen without explicit user approval.
- The LB Labs search API (`/recording-search/json`) is the primary search tool — it uses Typesense with fuzzy matching and handles classical titles well.
- Rate limits: LB API returns `X-RateLimit-Remaining` headers; the client sleeps automatically when near the limit. LB Labs has no explicit rate limit but be reasonable.

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

The translation cache at `~/.cache/lb-mapper/translations.json` is a flat JSON object that stores alias pairs in BOTH directions:

- **JA → EN** (primary use): scrobbled CJK artist / title → English equivalent, e.g. `"椎名林檎" → "Sheena Ringo"`, `"ゴルトベルク変奏曲 BWV 988: アリア" → "Goldberg Variations, BWV 988: Aria"`
- **EN → native** (fallback): romanized Japanese title or English band name → native-script equivalent, e.g. `"Noudouteki Sanpunkan - 3 Min." → "能動的三分間"`, `"Tokyo Incidents" → "東京事変"`

The cache is bidirectional by convention — add entries in whichever direction is useful. Lookup is a plain `cache.get(key)` in either direction.

## Pipeline

```text
Phase 1  Fetch unlinked listens
Phase 2  Translate CJK → EN (primary)
Phase 3  Primary search (LB Labs Typesense)
Phase 4  Weak-match recovery (4a simplified artist · 4b EN→native · 4c MB direct API)
Phase 5  Evaluate matches (LLM, parallel when ≥ 50 items)
Phase 6  Present for approval
Phase 7  Execute approved mappings + deletions
```

All retry / recovery strategies live in the orchestration layer (this skill). The Python `search_batch` script is intentionally simple — it runs one primary query per item with a single CJK-artist fallback. Any further recovery is the orchestrator's job.

### Phase 1: Fetch

```bash
uv run python -m lb_mapper.cli.fetch_listens COUNT
```

Paginates history until `COUNT` **unlinked** listens are collected. Outputs JSON on stdout: `{total, linked, unlinked: [...]}`. Each listen has `listened_at`, `recording_msid`, `artist`, `track`, `release`. Report totals to the user.

### Phase 2: Translate CJK → EN

Load the cache. For each unlinked listen, use `contains_cjk()` from `lb_mapper.lb_search` to detect CJK / Hangul / Kana in both `artist` and `track`.

For each CJK string:

1. Cache hit → use cached translation.
2. Cache miss → translate now. The string may be:
   - A katakana **transliteration** of a Western name (e.g. `アルフレッド・ブレンデル` → `Alfred Brendel`).
   - A **native** kanji / hiragana name (e.g. `椎名林檎` → `Sheena Ringo`).
   - A **localized** classical title (e.g. `ピアノ協奏曲 第3番 ニ短調 作品30` → `Piano Concerto No. 3 in D minor, Op. 30`).
   - **Mixed-script** where only CJK portions need translation; preserve Latin segments verbatim.
3. Persist new translations back to the cache.

Delegate bulk translation to Codex MCP when available.

### Phase 3: Primary Search

Build one query per listen:

```json
{
  "artist": "<translated if CJK else original>",
  "track":  "<translated if CJK else original>",
  "release": "<original release or empty>",
  "original_artist": "<original CJK artist, else empty>"
}
```

Batch in chunks of ~50 and pipe to stdin:

```bash
echo '$JSON_ARRAY' | uv run python -m lb_mapper.cli.search_batch
```

The script runs one search per item against LB Labs (Typesense). If `original_artist` is CJK and the translated search returns zero results, it retries once with the original CJK artist. It returns `{...input, results: [...]}` per item; each result has `recording_mbid`, `recording_name`, `release_name`, `release_mbid`, `artist_credit_name`, `artist_credit_id`.

### Phase 4: Weak-Match Recovery

Before evaluation, attempt one more search for items that are likely recoverable:

#### 4a. Simplified-artist retry

When the primary search returned zero results AND the artist field is a multi-artist credit (contains `&`, `,`, or `feat.`), re-search with just the first artist. This rescues classical recordings whose Typesense index entry doesn't include the full "soloist, orchestra & conductor" string.

```python
# first artist = re.split(r'\s*[,&]\s*|\s+feat\.\s+', artist)[0].strip()
```

#### 4b. Reverse-direction (EN → native) retry

Trigger this retry **proactively** whenever the artist has an EN→native entry in the translation cache, without waiting for the primary search to fail. The primary search and reverse-direction search run as siblings; whichever yields a clean match wins. MB credits for Japanese acts are often indexed under native script only, so a Latin-form primary search may return nothing *or* return Typesense fallback hits (any track by the artist) that look like matches but aren't.

Also trigger reverse retry when:

- The artist looks like a well-known Japanese act by English name (`Tokyo Incidents`, `Sheena Ringo`, `Yuki Kajiura`, `SawanoHiroyuki[nZk]`, `YOASOBI`, `Ado`, `Hikaru Utada`, etc.) and the track title is already CJK. Search with `(native_artist, original_CJK_track)`.
- The track title looks like romanized Japanese (e.g. `Noudouteki Sanpunkan`, `Hatsukoiwa Makekaku`, `Sakura`) and the reverse romaji→native translation can be inferred with reasonable confidence.

Store new reverse translations in the cache so subsequent runs short-circuit. If reverse translation is uncertain, flag the listen for review rather than guessing.

#### 4c. MB direct API fallback (critical for classical and Japanese catalog)

**LB Labs Typesense and the MusicBrainz direct API have very different recall.** Empirical finding from a 500-listen run: dozens of recordings (Jan Lisiecki K.271 movements, Renaud Capuçon BWV 1001, 椎名林檎 いとをかし, Tokyo Incidents O.S.C.A., 和久井沙良 幽霊になっても美しい, …) **do exist in MB** but never surface from LB Labs Typesense. They surface immediately from the MB Lucene index.

For any listen still without a confident match after Phase 3 + 4a + 4b, query MB directly:

```text
GET https://musicbrainz.org/ws/2/recording/?query=<lucene>&fmt=json&limit=10
```

Use a `User-Agent` header (`lb-mapper/<version> (<contact>)`) and respect the **1 request / second** rate limit.

Build the Lucene query with:

- `artist:"<name>"` — exact phrase, prefer the translated English / native form that MB indexes under (e.g. `Jan Lisiecki`, not `ヤン・リシエツキ`; `椎名林檎`, not `Sheena Ringo`).
- 2–4 distinctive `recording:<token>` clauses joined with `AND`. Pick:
  - Catalog tag + number first: `BWV 1001`, `K. 271`, `Op. 52`, `WoO 31`, `MWV O14`, `D. 550`.
  - Movement marker if present: `Fuga`, `Rondeau`, `Andantino`.
  - Distinctive title word for non-classical: `OSCA`, `いとをかし`, `幽霊`.
- Avoid stuffing too many tokens — MB Lucene treats `AND` strictly, so over-constrained queries return zero. 3–4 tokens is a sweet spot.

Score interpretation:

- MB returns a `score` per recording. `score=100` is necessary but **not sufficient** — Lucene treats partial token matches as 100. Always verify the returned `recording.title` actually matches the listen's track identity (work + catalog + key + movement) using the Phase 5 rules.
- A genuine match: catalog number aligns exactly, movement marker matches, key matches.
- A false hit: same artist + same opus number on a *different* opus + different movement (Lucene matched the artist + the integer "26" but `op. 26 no. 3` ≠ `Op. 26 No. 1`).

Skip the MB fallback when the artist is a known katakana-only obscure act (Phase 5 Case A) — MB direct won't have these either.

#### 4d. Merge

Merge recovered results from 4a / 4b / 4c into the primary result set, preserving provenance (`retry_used`, `mb_direct`) so Phase 5 can weight them. MB direct hits should be presented to the evaluator as the *top* candidates because they're far more selective than Typesense fallbacks.

### Phase 5: Evaluate Matches

For each listen, decide the verdict using the top ~5 candidates. Do NOT use hardcoded thresholds — apply the domain rules below as reasoning steps.

When the batch is ≥ 50 items, parallelize: split into chunks of ~100, spawn one evaluator per chunk (Codex MCP session or Claude Code subagent), and merge JSON verdicts.

#### Artist Verification

- `artist_credit_name` must plausibly refer to the same artist(s) as the listen.
- Separator variations (`&`, `,`, `feat.`, `and`) are equivalent.
- Minor spelling / accent differences are OK (e.g. `Capuccelli` ≈ `Capucelli`).
- **Substring false positives are mismatches**: `Foster` ≠ `Neil Foster` ≠ `Kendra Foster`.
- CJK artists may appear directly in MB credits as aliases — check both the original and the translation against the credit.
- For romanized Japanese acts, check both the English form and the native form (e.g. `Tokyo Incidents` AND `東京事変`).

#### Title Matching — General

- Same recording, not just overlapping words.
- Short / generic titles (`Alive`, `Home`, `Love`, `Shine`) need stronger artist + release corroboration.
- **One-sided annotations** — `(TV Edit)`, `[Deluxe]`, `(Arr. for Piano)`, `(feat. X)`, `(Live)` — are usually noise. Accept if the underlying work matches.
- **Two-sided conflicts** — e.g. listen `"Orchestral Version"` vs match `"Acoustic"` — indicate different recordings; reject unless release context reconciles.

#### Title Matching — Classical Music (strict)

Classical titles encode precise work identity. Two recordings that share a generic word (`Allegro`, `Sonata`, `Prelude`) but differ in any of the following are **different works**:

- **Catalog numbers**: Op./Opus, K./KV (Mozart), BWV (Bach), TWV (Telemann), HWV (Handel), RV (Vivaldi), Wq (C.P.E. Bach), D. (Schubert), S. (Liszt), Hob. (Haydn), WoO (Beethoven works without opus number), MWV (Mendelssohn).
- **Work number within an opus**: `No. 2` vs `No. 1` in the same opus → different work.
- **Key signature**: `C major` vs `B-flat minor` → different work.
- **Movement marking**: if the listen specifies `II. Allegro` and the candidate is `III. Allegro giocoso` or the whole work, that's a mismatch.

When a Bach / Mozart / Beethoven / etc. catalog mismatch exists (e.g. listen says `BWV 1178`, all candidates say `WoO 31`), reject even though the artist matches — see deletion rule below.

##### Rejections

- Listen `Nocturne in E-flat major, Op. 9 No. 2` vs match `Nocturne in B-flat minor, Op. 9 No. 1` — same opus, different nocturne and key.
- Listen `Violin Concerto in D major, Op. 77: II. Adagio` vs match `... III. Allegro giocoso` — wrong movement.
- Listen `Prelude and Fugue in C major, BWV 846` vs match `... in C minor, BWV 871` — both C-something preludes-and-fugues, but WTC Book I vs Book II.
- Listen `Chaconne and Fugue in D Minor, BWV 1178` vs match `Fugue in D Major, WoO 31` — different composer catalog (Bach vs Beethoven).

##### Acceptances

- Listen `Lakme, Act 1: Duo des fleurs (Transcr. Ducros for Cello Ensemble) [Classical Session]` vs match `Lakme: Act 1: Duo des fleurs` — arrangement and release annotations are noise.
- Listen `Mozart: Piano Sonata K. 331: III. Rondo alla Turca` vs match `Piano Sonata no. 11 in A major, KV 331: III. Alla turca` — `K.` ≡ `KV`; `Rondo alla Turca` is the nickname for the `Alla turca` movement.
- Listen `Beethoven: Moonlight Sonata: I. Adagio sostenuto` vs match `Piano Sonata no. 14 in C-sharp minor, op. 27 no. 2: I. Adagio sostenuto` — `Moonlight Sonata` = Op. 27 No. 2.
- Listen `Schubert: Die Forelle, D. 550` vs match `The Trout, op. 32, D 550` — same lied; matching D. number confirms identity despite language.

#### CJK / Katakana Handling

##### Katakana-only artist

A **katakana-only artist** is one whose core name is pure katakana (transliterating a non-Japanese name). Standard Japanese ensemble / instrument suffixes in kanji — `四重奏団` (quartet), `管弦楽団` (orchestra), `室内管弦楽団` (chamber orchestra), `交響楽団` (symphony orchestra), `合唱団` (choir) — do NOT disqualify it. The core name is still a transliteration.

Verdict rule — **two cases**:

**Case A: Japan-only obscure release → `delete`**

When all of these hold:

- Katakana-only artist.
- Primary + simplified-artist retry + CJK-artist fallback + EN→native retry all fail to produce a candidate that survives the evaluation rules.
- The translated English form of the artist is NOT a mainstream internationally-active artist (see below).
- AND the recording is plausibly a Japan-exclusive release (anime tie-in single, bonus track, regional edition, world premiere).

Rationale: these are bad scrobbles from Japanese streaming services whose metadata never propagated into MusicBrainz. Keeping them as `skip` re-evaluates the same doomed candidates on every future run.

**Case B: Mainstream Western artist in katakana → `skip`, not `delete`**

A katakana artist name is ALSO how Japanese scrobblers (SmashTunes, Apple Music JP) render mainstream Western artists. These recordings WILL propagate to MB over time — deleting them discards data that would otherwise map on a later run.

Treat as `skip` when the translated English name is a well-known international artist, for example:

- Classical soloists: Alice Sara Ott, Renaud Capuçon, Jan Lisiecki, Alfred Brendel, Arthur Grumiaux, Lang Lang, Herbert von Karajan, Leonard Bernstein.
- Major orchestras / conductors: Vienna Philharmonic, Berlin Philharmonic, Orchestre de la Suisse Romande, Paavo Järvi, Claudio Abbado.
- Contemporary / crossover composers: Max Richter, Ludovico Einaudi, Nils Frahm.
- Rock / pop: The Rolling Stones, Muse, Dream Theater, Helloween.

Heuristic: if you'd be surprised that the artist has no MusicBrainz entry at all, it's Case B (skip). The track isn't in MB *yet*, but the artist obviously is.

**Important**: "no candidate survives evaluation" must actually verify candidates against the track title, not just the artist. LB Labs Typesense returns fuzzy fallback hits — ANY track by the artist whose title vaguely resembles the search — when the exact track isn't indexed. Always check `recording_name` matches the listen's track identity (work + catalog + movement + key), not just that the artist lines up.

##### Native-script artist (kanji / hiragana)

Artists whose core name is kanji or hiragana (e.g. `椎名林檎`, `藤田真央`, `街風めい`, `辻井伸行`, `角野隼斗`) are **native Japanese artists** with legitimate MB entries. Do NOT auto-delete. If no match is found, verdict is `skip`.

##### CJK-localized track title

A listen with a CJK-translated track title (e.g. `ピアノ協奏曲 第3番 ニ短調 作品30`) that yields no evaluation-compatible candidate after title translation AND reverse-direction retry → `delete`. These are localized classical metadata; if even the English work title doesn't find the recording, it's unlikely to exist in MB.

##### Mixed scripts

Artists with mixed katakana + Latin (e.g. `キャロル&チューズデイ(Vo.Nai Br.XX&Celeina Ann)`) are NOT auto-deleted; many have legitimate MB entries.

#### Verdict Categories

| Verdict  | Meaning                                                                                                                                                                                                                                                      |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `link`   | Confident match: same work, compatible artist, title identifies the same recording.                                                                                                                                                                          |
| `review` | Plausible but ambiguous: short title, larger ensemble credit, arrangement differences, partial title overlap.                                                                                                                                                |
| `skip`   | No usable match. Native-Japanese artist with no hit, mainstream Western artist transliterated to katakana whose specific recording isn't in MB yet, or non-CJK listen with weak candidates. Leave for future runs.                                           |
| `delete` | Bad listen that will never match. Triggers: (1) katakana-only artist with NO MB presence at all (obscure, Japan-only) + no candidate survives evaluation, (2) CJK-localized track title with no evaluation-compatible candidate after translation + retries. |

**Skip vs delete boundary**: the question is *"does this artist have MB presence that a future search could find?"* Note: the LB Labs Typesense index returns fuzzy fallback hits — any track by the artist — whenever the exact track isn't indexed. So "candidates exist" is not evidence that the specific recording is in MB. Verify `recording_name` actually matches the listen's track identity (work + catalog + movement + key), not just that the artist lines up.

If the artist name translates to a mainstream international artist (Max Richter, Renaud Capuçon, Alice Sara Ott, Lang Lang, Vienna Philharmonic, …) → **skip**, because future MB syncs will eventually add the specific recording.

If the artist translates to something obscure that has no MB presence at all (niche vocaloid producer, one-off anime composer) → **delete**, because the scrobble will never map.

### Phase 6: Present for Approval

Group by verdict. Never execute without explicit user approval.

**Links (N items)** — each:

```text
[artist] — [track]
  -> [match_artist] — [match_title] ([recording_mbid])
```

**Reviews (N items)** — each with a one-line note:

```text
[artist] — [track]
  -> [match_artist] — [match_title] ([recording_mbid])
  Note: [reason for uncertainty]
```

**Deletions (N items)** — each with a reason:

```text
[artist] — [track] (listened_at: [timestamp])
  Reason: [why this should be deleted]
```

**Skips (N items)** — list briefly.

For large batches (> ~30 items per group), write the full detail to a temp file (e.g. `/tmp/review.md`) and show a summary + file path in the chat. Always show reviews and deletions inline since they require judgement.

User options:

- Approve all
- Approve links only
- Approve links + deletions, defer reviews
- Cherry-pick by idx
- Override verdicts (promote review → link, or demote link → skip)
- Re-evaluate specific items

### Phase 7: Execute

After approval:

```bash
echo '{"mappings": [...], "deletions": [...]}' | \
    uv run python -m lb_mapper.cli.execute
```

- Each mapping: `{recording_msid, recording_mbid}`.
- Each deletion: `{listened_at, recording_msid}`.

The script handles rate limits internally and reports per-action progress.

## Parallelism

For batches ≥ 50 items during Phase 5, parallelize:

1. Split listens into ~100-item chunks.
2. Prepare one input JSON file per chunk with listens + top-5 candidates.
3. Spawn one evaluator per chunk — Codex MCP session preferred, Claude Code subagent as fallback.
4. Each evaluator writes a verdict JSON `[{idx, verdict, recording_mbid, reason}, ...]`.
5. Merge by `idx` after all complete.

## Important Notes

- NEVER submit a mapping or delete a listen without explicit user approval.
- Two search backends are used in parallel:
  - **LB Labs Typesense** (`/recording-search/json`): fast batch search, fuzzy matching. Good first pass but returns *fallback hits* — any track by the artist whose title vaguely resembles the query — when the exact recording isn't indexed. These look like matches but aren't.
  - **MusicBrainz Lucene direct** (`musicbrainz.org/ws/2/recording/`): precise field-targeted queries with `artist:"…" AND recording:<token>` clauses. Strict 1 req/s rate limit. Use as Phase 4c fallback when LB Labs misses or returns garbage.
- Rate limits: LB API returns `X-RateLimit-Remaining` headers; the Python client sleeps automatically when near the limit. LB Labs has no explicit rate limit. MB direct is 1 req/sec — sleep 1.05s between calls.
- The translation cache grows across runs — early runs are slower, later runs are faster.
- When in doubt, prefer `skip` over `delete` for non-CJK listens. Deletion is irreversible.

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

Code runs via `uv run` from the repo root. CLI helpers in `lb_mapper.cli`:

- `fetch_listens` — fetch recent listens, output unlinked as JSON
- `search_batch` — batch search LB Labs for recording matches
- `execute` — submit approved mappings and delete approved listens

The translation cache at `~/.cache/lb-mapper/translations.json` is a flat JSON object keyed by either direction. Always write **both** directions when you learn an alias pair: `cache["椎名林檎"] = "Sheena Ringo"` and `cache["Sheena Ringo"] = "椎名林檎"`. Lookup is `cache.get(key)`. The cache grows across runs; later runs are faster.

## Pipeline

```text
Phase 1  Fetch unlinked listens
Phase 2  Translate CJK strings (both directions)
Phase 3  Search (primary + every recovery angle)
Phase 4  Evaluate matches
Phase 5  Reconcile verdicts (Case A/B + skip-recovery sweep)
Phase 6  Present for approval
Phase 7  Execute approved actions
```

All retry / recovery logic lives in the orchestration layer. The Python `search_batch` script is intentionally simple — it runs one primary query per item with one CJK-artist fallback. Anything beyond is the orchestrator's job.

## Phase 1: Fetch

```bash
uv run python -m lb_mapper.cli.fetch_listens COUNT
```

Paginates history until `COUNT` **unlinked** listens are collected. Outputs `{total, linked, unlinked: [...]}` on stdout. Each listen has `listened_at`, `recording_msid`, `artist`, `track`, `release`. Report totals to the user.

## Phase 2: Translate

Load the cache. Use `contains_cjk()` from `lb_mapper.lb_search` to detect CJK / Hangul / Kana in `artist` and `track`.

For each CJK string, look up the cache; for misses, translate now. Strings fall into one of:

- **Katakana transliteration** of a Western name → recover the original (`アルフレッド・ブレンデル` → `Alfred Brendel`).
- **Native** kanji / hiragana name → canonical romanization.
- **Localized classical title** → standard English form preserving the catalog tag.
- **Mixed-script** → translate only the CJK portions; preserve Latin segments verbatim.

Persist new entries to the cache in both directions when applicable.

**Delegate bulk translation to Codex MCP and instruct it to use web search.** Training-data recall produces plausible-sounding but wrong titles. Always verify via Apple Music JP / MusicBrainz / the artist's official discography page for: romaji ↔ native kanji inference, Apple Music JP `Romaji - English` title splits, well-known JP discography lookups, and classical work / catalog verification.

## Phase 3: Search

For every listen, gather candidates by trying **all the angles below** and merging the results. Don't stop after the first hit — fuzzy fallbacks look like matches but often aren't, so a real match found by a later angle wins.

### 3a. Primary LB Labs query

```bash
echo '$JSON_ARRAY' | uv run python -m lb_mapper.cli.search_batch
```

Per item: `{artist, track, release, original_artist}` where `artist` / `track` are translated forms and `original_artist` is the original CJK if applicable. The script does one Typesense search per item plus one CJK-artist fallback. Returns `{...input, results: [...]}` per item.

### 3b. Reverse-direction (EN → native) retry

Run **proactively** as a sibling to 3a. MB credits for Japanese acts are often indexed under native script only, so a Latin-form primary search may return nothing or — worse — return Typesense fallback hits that look like matches.

Trigger when:

- The artist has an EN → native entry in the cache.
- The artist is a well-known JP act by English name (`Tokyo Incidents`, `Sheena Ringo`, `Maaya Sakamoto`, `Yoko Kanno`, `Mitsuki Takahata`, `Sakuzyo` / `Sakujo`, `Mika Kobayashi`, `Ryosuke Nagaoka`, `Ceui`, `SawanoHiroyuki[nZk]`, `Yuki Kajiura`, `YOASOBI`, `Ado`, `Hikaru Utada`, `Hiroyuki Sawano`, `Promise of wizard`).
- Track title looks like romanized Japanese — infer the native kanji via web search.
- Track is formatted `Romaji - English Translation` (Apple Music JP convention). Split on ` - `, use the romaji half for inference, discard the English (it rarely appears in MB).

Search with `(native_artist, native_track)`. If reverse translation is uncertain, delegate to Codex MCP with explicit web-search instruction. Persist new aliases in both directions.

### 3c. Simplified-artist retry

When the artist field is a multi-artist credit (`&`, `,`, `feat.`) and the primary returned zero or only fuzzy hits, re-search with just the first artist. This rescues classical recordings whose Typesense entry doesn't include the full "soloist, orchestra & conductor" string.

```python
first = re.split(r'\s*[,&]\s*|\s+feat\.\s+', artist)[0].strip()
```

### 3d. Featured-artist rewrap

JP / anime scrobbles frequently shuffle primary vs featured artists relative to MB. For each `feat. X` in either field, run an LB Labs query with `X` as primary artist + the stripped track. Two patterns:

- **Listen primary becomes MB featured**: listen `ペトロールズ — 雨` is indexed in MB as `dropp — 雨 feat. ペトロールズ`.
- **Listen featured becomes MB primary**: listen `削除 — 怪獣になりたい (feat. 初音ミク)` is indexed as `Sakuzyo feat. 初音ミク — 怪獣になりたい`.

Any retry whose `artist_credit_name` mentions the listen's primary artist (as either main or featured) is a valid candidate.

### 3e. MB direct API

When 3a–3d still leave an item without a confident candidate (zero hits, or only fuzzy fallbacks where `recording_name` doesn't match the listen's track identity), query MB Lucene directly:

```text
GET https://musicbrainz.org/ws/2/recording/?query=<lucene>&fmt=json&limit=10
User-Agent: lb-mapper/<version> (<contact>)
```

Strict **1 req / sec** rate limit; sleep 1.05s between calls.

LB Labs Typesense and MB direct have very different recall. Many recordings exist in MB but never surface from Typesense; they surface immediately from the Lucene index.

#### Building the Lucene query

- `artist:"<name>"` — exact phrase. Prefer the form MB indexes under (`Jan Lisiecki`, not `ヤン・リシエツキ`; `椎名林檎`, not `Sheena Ringo`).
- 2–4 distinctive `recording:<token>` clauses joined with `AND`. Pick:
  - Catalog tag + number first (`BWV 1001`, `K. 271`, `Op. 52`, `WoO 31`).
  - Movement marker if present (`Fuga`, `Rondeau`, `Andantino`).
  - Distinctive title word for non-classical.
- 3–4 tokens is the sweet spot. MB Lucene treats `AND` strictly — over-constrained queries return zero.

Query priority order for items still missing after 3a–3d:

1. Native artist + native track.
2. Native artist + ASCII tokens when track is mixed-script. Subtitle decorations don't have to appear in the query — verify in post-check.
3. Romanized artist + romaji track (MB indexes both spellings for some acts, e.g. `Sakuzyo`).
4. Try both kanji homophones for romaji titles (`Mokuren` → both `木蘭` and `木蓮`).

#### Title simplification before tokenizing

Listen titles carry decorations that don't appear in MB. Strip them so the `AND`-joined query matches:

- Strip parentheticals: `(Remastered 2024)`, `(Live)`, `(feat. X)`, `(Arr. ...)`, `(TV Edit)`, `(Extend ver.)`, `[Deluxe]`.
- Drop the colon-tail for classical: `Op. 10: No. 1 in F-Sharp Minor` → keep `Op. 10 No. 1`. Movement markers after the colon are phrased variably; narrow by catalog + ordinal, verify movement in post-check.
- Use ordinals as filters, not search terms. `recording:"No. 1"` is too literal — use bare `recording:op AND recording:10 AND recording:1`.
- Try the bare catalog alone when artist + catalog + one distinctive word returns zero.

#### Lucene quoting pitfalls

- Diacritics in quoted phrases break the search. ASCII-fold or drop the quotes when the name has accents.
- Don't quote catalog tokens. MB indexes them variably (`op. 10`, `Op. 10`, `Opus 10`).
- Periods and spaces inside quoted phrases trigger literal matching.

#### Score interpretation

`score=100` is necessary but not sufficient — Lucene treats partial token matches as 100. Always verify the returned `recording.title` matches the listen's track identity using the Phase 4 rules. Common false hit: same artist + same opus number on a different opus + different movement.

### 3f. Merge

Merge candidates from all angles, dedup by `recording_mbid`, preserve provenance (`tag` field: `lb`, `lb-reverse`, `mb`, `4a`). MB-direct hits go to the top of the candidate list — far more selective than Typesense fallbacks.

## Phase 4: Evaluate

For each listen, decide a verdict using the top ~5 candidates and the rules below. No hardcoded thresholds — apply the rules as reasoning steps. Batches ≥ 50 items: parallelize into ~100-item chunks across Codex MCP sessions or Claude Code subagents; each chunk writes `[{idx, verdict, recording_mbid, reason}, ...]`; merge by `idx`.

### Verdict categories

| Verdict  | Meaning                                                                                                                                                                                    |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `link`   | Confident match: same work, compatible artist, title identifies the same recording.                                                                                                        |
| `review` | Plausible but ambiguous: short title, larger ensemble credit, arrangement differences, partial title overlap.                                                                              |
| `skip`   | No usable match — but the *recording* may still propagate to MB on a future sync. Default for native-script JP artists, non-classical katakana scrobbles, Latin-only with weak candidates. |
| `delete` | The specific recording will not propagate. Triggers detailed under "CJK / katakana handling" below.                                                                                        |

The skip vs delete question is *"will this specific recording propagate to MB on a future sync?"* — not "does the artist have MB presence?". A famous artist's Apple-Music-JP exclusive may never appear in MB despite the artist having a rich MB catalog. The mainstream-artist heuristic protects only non-classical tracks (Case B default).

### Artist verification

- `artist_credit_name` must plausibly refer to the same artist(s) as the listen.
- Separator equivalence: `&`, `,`, `feat.`, `and`, `vs`, `vs.` are interchangeable. JP hardcore / dōjin scrobbles often render `vs` as `&`.
- Minor spelling / accent differences OK.
- Substring false positives are mismatches: `Foster` ≠ `Neil Foster`.
- For romanized JP acts, check both English and native forms against the credit.

#### Artist-equivalence rules

- Listen artist appears as `feat. <listen_artist>` inside the candidate's credit.
- Listen artist appears as the primary given name only, surname stripped — Japanese romanized given-name only. Do not apply to Western names.
- EN ↔ native variants of the same person via the cache.
- Romanization-system swaps (Kunrei-shiki ↔ Hepburn): `Sakujo` ≡ `Sakuzyo`, `si` ≡ `shi`, `tu` ≡ `tsu`, `tyu` ≡ `chu`.
- Composer ↔ performer rewrap: when MB credits the performing vocalists / ensemble but the listen credits the composer, accept if recording identity holds.

### Title matching

- Same recording, not just overlapping words.
- Generic short titles (`Alive`, `Home`, `Love`, `Shine`) need stronger artist + release corroboration.
- One-sided annotations are noise: `(TV Edit)`, `[Deluxe]`, `(Arr. for Piano)`, `(feat. X)`, `(Live)`, `(Remastered 20XX)`, `(Recast 20XX)`, `(Extend ver.)`, `(Anime Ver.)`, `(Transcr. for ...)`.
- Two-sided conflicts indicate different recordings — listen `Orchestral Version` vs candidate `Acoustic` rejects.
- Remaster equivalence: `(Remastered 20XX)` / `(Recast 20XX)` / `(XX Edition)` map to the original-recording MBID when no dedicated remaster MBID exists, provided artist + work + key + movement otherwise match.
- Unicode hyphen / dash equivalence: ASCII `-` (U+002D) ≡ `‐` (U+2010) ≡ `‒` ≡ `–`.
- Instrumental annotation equivalence: `(without vocals)`, `(without 娘々)`, `(instrumental)`, `(off-vocal)`, `(karaoke)` match MB recordings tagged with the same kind of annotation.
- Kanji homophones: a romaji title can map to multiple kanji renderings (e.g. `Mokuren` → either `木蓮` or `木蘭`). When this is the only difference and artist + release match, treat as a link.

### Classical music — strict identity

Classical titles encode precise work identity. Two recordings that share a generic word (`Allegro`, `Sonata`, `Prelude`) but differ in any of the following are **different works**:

- **Catalog numbers**: Op., K. / KV (Mozart), BWV (Bach), TWV (Telemann), HWV (Handel), RV (Vivaldi), Wq (C.P.E. Bach), D. (Schubert), S. (Liszt), Hob. (Haydn), WoO (Beethoven without opus), MWV (Mendelssohn), B. (Dvořák).
- **Work number within an opus**: `No. 2` vs `No. 1` in the same opus → different work.
- **Key signature**: different key → different work.
- **Movement marking**: listen `II. Allegro` vs candidate `III. Allegro giocoso` is a mismatch.
- **Composer-catalog mismatch** (listen `BWV 1178` vs candidate `WoO 31`) → reject even when artist matches.

Cross-catalog equivalences: `K.` ≡ `KV`; nicknames ≡ formal titles (`Moonlight Sonata` ≡ `Op. 27 No. 2`; `Für Elise` ≡ `Bagatelle WoO 59`; `Die Forelle` ≡ `The Trout, D. 550`).

### CJK / katakana handling — the skip / delete boundary

A **katakana-only artist** has a core name in pure katakana (transliterating a non-Japanese name). Standard kanji ensemble suffixes (`四重奏団` quartet, `管弦楽団` orchestra, `室内管弦楽団` chamber orchestra, `交響楽団` symphony orchestra, `合唱団` choir) do not disqualify it.

**Case A: katakana artist + classical track → `delete`** when no candidate matches the work identity (catalog + movement + key) after Phase 3 has exhausted every angle. This applies even to mainstream classical artists. Apple Music JP / SmashTunes album-track scrobbles for any classical artist do not propagate to MB on later syncs.

**Case B: katakana artist + non-classical track → `skip` by default**. Promote to `delete` only when the title carries an explicit arrangement marker (`(Arr. for ...)`, `(Transcr. for ...)`, `(After [Composer]'s [Work])`) OR web search confirms a classical work behind a colloquial title, AND the verified-classical re-query also returns no MB match.

**Native-script artist (kanji / hiragana)** — never auto-delete; their recordings DO propagate. Verdict is `skip` when no match is found.

**CJK-localized classical title on a non-katakana artist** — `delete` when no candidate matches after title translation AND reverse-direction retry. If neither the English nor the native form finds the recording, it won't appear.

**Mixed-script artists** (katakana + Latin) — not auto-deleted; many have legitimate MB entries.

When deciding "no candidate survives", you must verify candidates against the **track title**, not just the artist. Typesense returns fuzzy fallback hits — any track by the artist whose title vaguely resembles the search. Always check `recording_name` matches the listen's track identity (work + catalog + movement + key).

## Phase 5: Reconcile

Phase 4 evaluators (especially parallel chunks) are systematically biased toward `skip` and miss most Case A `delete` candidates and some recoverable links. The orchestrator owns the final boundary.

### 5a. Promote skip → delete (Case A)

For each `skip`, promote to `delete` when **all** hold:

- Listen artist is katakana-only OR the track title is CJK-localized classical metadata.
- Track is clearly classical:
  - **Catalog marker** present: `BWV`, `K.`, `KV`, `Op.`, `D.`, `S.`, `Hob.`, `WoO`, `MWV`, `HWV`, `RV`, `TWV`, `Wq`, `Sz.`, `L.`, `FP`, `H.`, `M.`, `B.`, `CD`.
  - OR **form word**: `Sonata`, `Concerto`, `Symphony`, `Prelude`, `Préludes`, `Fugue`, `Nocturne`, `Étude`, `Variations`, `Chaconne`, `Partita`, `Suite`, `Quartet`, `Quintet`, `Waltz`, `Scherzo`, `Mazurka`, `Ballade`, `Impromptu`, `Fantaisie`, `Fantasien`, `Intermezzo`, `Capriccio`, `Toccata`, `Rondo`, `Minuet`, `Polonaise`, `Cantata`, `Romance`, `Barcarolle`, `Bagatelle`, `Kinderszenen`, `Davidsbündlertänze`, `Stimmungsbilder`, `Bergamasque`, `Träumerei`, `Tombeau`, `Gymnopédie`, `Divertimento`, `Sinfonia`.
  - OR **composer-specific localized title** (`スラヴ行進曲` = Slavonic March, `朱色の塔` = Torre Bermeja).
- No surviving candidate matches the work identity — only Typesense fuzzy fallbacks.

This is a regex match — be aggressive. Add new catalog markers and form words as you discover them.

### 5b. Web-verify Case B candidates

For `skip` items where the artist is katakana-only and the track is non-classical-looking, delegate per-item web verification to Codex with explicit `Use web search` instruction. Classify each as classical / non-classical. For confirmed-classical items, re-query MB Lucene with the canonical composer + work. Promote to `delete` only when the work is verified-classical AND the MB re-query also fails.

### 5c. Demote delete → skip

- Demote when the listen's artist has no CJK at all (Latin-only name) — deletion is too aggressive without script evidence of Apple Music JP origin.
- Keep `skip` for native-script (kanji / hiragana) artists — their specific recordings may still propagate.

### 5d. Skip-recovery sweep

For every remaining `skip` whose Phase 3 candidates are Typesense fallbacks (top hit's `recording_name` doesn't match `track_orig`), run an MB-direct query using the priority order from Phase 3e. This is the highest-yield late-stage recovery — Phase 3e was invoked for items with **zero** Phase 3 hits, but listens with fuzzy fallbacks never reached it.

After the MB-direct query, if a clean match appears, promote `skip` → `link`. Persist any new aliases discovered (especially the EN ↔ native pair) to the cache in **both directions**.

Be conservative on demotion, aggressive on Case A promotion. Native-script artists with no MB match after the sweep stay `skip` — deletion is permanent and these artists' other recordings DO appear in MB.

## Phase 6: Present

Group by verdict. Never execute without explicit user approval.

```text
Links (N items):
  [artist] — [track]
    -> [match_artist] — [match_title] ([recording_mbid])

Reviews (N items):
  [artist] — [track]
    -> [match_artist] — [match_title] ([recording_mbid])
    Note: [reason for uncertainty]

Deletions (N items):
  [artist] — [track] (listened_at: [timestamp])
    Reason: [why this should be deleted]

Skips (N items): list briefly.
```

For groups > ~30 items, write the full detail to `/tmp/review.md` and show a summary + file path. Always show reviews and deletions inline — they require judgement.

User options: approve all · approve links only · approve links + deletions, defer reviews · cherry-pick by idx · override verdicts · re-evaluate specific items.

## Phase 7: Execute

```bash
echo '{"mappings": [...], "deletions": [...]}' | uv run python -m lb_mapper.cli.execute
```

- Each mapping: `{recording_msid, recording_mbid}`.
- Each deletion: `{listened_at, recording_msid}`.

The script handles rate limits internally and reports per-action progress. A transient HTTP 500 on a deletion or mapping is non-fatal — retry once after a short delay.

## Important notes

- **NEVER** submit a mapping or delete a listen without explicit user approval.
- LB Labs Typesense returns fuzzy fallback hits — always cross-check `recording_name` against the listen's track identity.
- MB direct (`musicbrainz.org/ws/2/recording/`) has strict 1 req/sec; sleep 1.05s between calls.
- LB API rate limits are surfaced via `X-RateLimit-Remaining`; the Python client sleeps automatically.
- When in doubt for non-CJK listens, prefer `skip` over `delete`. Deletion is irreversible.
- Evaluators that omit `recording_mbid` on non-link entries are valid — normalize before merging.

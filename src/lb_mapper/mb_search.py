"""MusicBrainz recording search and best-match selection via direct API calls."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, TypedDict, cast

from lb_mapper.lb_client import create_http_client


MB_BASE_URL = 'https://musicbrainz.org/ws/2'
USER_AGENT = 'lb-mapper/0.1.0 (https://github.com/hakula/listenbrainz-auto-mapper)'
AUTO_ACCEPT_SCORE = 90

# Rate limiting: 1 request per second
_last_request_time: float = 0.0

_client = create_http_client(
    base_url=MB_BASE_URL,
    headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'},
)

# Patterns stripped from track names to improve matching
_STRIP_PATTERNS = [
    # Version / edit annotations: "-TV Edit -", "(TV Size)", "[Deluxe]", etc.
    r'\s*[-–—]\s*(TV|Radio|Album|Single|Live|Acoustic|Orchestral|Instrumental|Original|Deluxe|Bonus)\s+\w*\s*[-–—]?\s*$',
    r'\s*\((TV|Radio|Album|Single|Live|Acoustic|Orchestral|Instrumental|Original|Deluxe)\s*[^)]*\)\s*$',
    r'\s*\[(TV|Radio|Album|Single|Live|Acoustic|Orchestral|Instrumental|Original|Deluxe)\s*[^]]*\]\s*$',
    # Arrangement / transcription credits from Apple Music metadata
    # e.g., "(Arr. Cortot for Piano)", "(Transcr. for Guitar by Rosie Bennet)"
    r'\s*\(Arr\..*?\)\s*$',
    r'\s*\[Arr\..*?\]\s*$',
    r'\s*\(Transcr\..*?\)\s*$',
    r'\s*\[Transcr\..*?\]\s*$',
    # Version annotations: "(Version for Solo Piano)", "(Acoustic Guitar Cover)"
    r'\s*\(Version for [^)]+\)\s*$',
    r'\s*\([^)]+ Version\)\s*$',
    # Classical: strip trailing movement/tempo markings after colon
    # e.g., "Piano Concerto No. 21: Allegro" → "Piano Concerto No. 21"
    r':\s*(Allegro|Andante|Adagio|Moderato|Presto|Vivace|Largo|Grave|Lento|Scherzo|Minuet|Rondo|Finale)\b.*$',
    # Classical: strip trailing key signatures
    # e.g., "Sonata in A Major" → "Sonata"
    r'\s+in\s+[A-G][#b♯♭]?\s+(Major|Minor|major|minor|Maj|Min|maj|min)\s*$',
    # "feat." credits that Apple Music may include in track name
    r'\s*\(feat\.\s+[^)]+\)\s*$',
    r'\s*\[feat\.\s+[^]]+\]\s*$',
]
_STRIP_RE = [re.compile(p, re.IGNORECASE) for p in _STRIP_PATTERNS]

# Patterns stripped from release names (Apple Music conventions not used by MB)
_RELEASE_STRIP_PATTERNS = [
    # Apple Music appends "- Single" / "- EP" to release names
    r'\s*-\s*(Single|EP)\s*$',
    # Edition annotations: "(Deluxe Version)", "[Special Edition]", etc.
    r'\s*\((Deluxe|Special|Bonus|Extended)\s*(Version|Edition)?\)\s*$',
    r'\s*\[(Deluxe|Special|Bonus|Extended)\s*(Version|Edition)?\]\s*$',
]
_RELEASE_STRIP_RE = [re.compile(p, re.IGNORECASE) for p in _RELEASE_STRIP_PATTERNS]


def normalize_track_name(name: str) -> str:
    """Strip annotations, edit markers, and movement markings from a track name."""
    result = name
    for pattern in _STRIP_RE:
        result = pattern.sub('', result)
    return result.strip()


def normalize_release_name(name: str) -> str:
    """Strip Apple Music suffixes like '- Single', '- EP', '(Deluxe Version)'."""
    result = name
    for pattern in _RELEASE_STRIP_RE:
        result = pattern.sub('', result)
    return result.strip()


def _rate_limited_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)

    resp = _client.get(path, params=params)
    _last_request_time = time.monotonic()
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return data


# ── MusicBrainz recording search response types ──


class _MBArtistInfo(TypedDict, total=False):
    id: str
    name: str


class _MBArtistCredit(TypedDict, total=False):
    name: str
    joinphrase: str
    artist: _MBArtistInfo


class _MBRelease(TypedDict, total=False):
    id: str
    title: str


# Functional form required for the hyphenated 'artist-credit' key.
_MBRecording = TypedDict(
    '_MBRecording',
    {
        'id': str,
        'title': str,
        'score': int,
        'artist-credit': list[_MBArtistCredit],
        'releases': list[_MBRelease],
    },
    total=False,
)


class _MBSearchResult(TypedDict, total=False):
    recordings: list[_MBRecording]


@dataclass(frozen=True)
class RecordingMatch:
    mbid: str
    title: str
    artist_credit: str
    score: int
    release: str


def search_recordings(
    artist: str | None = None,
    recording: str | None = None,
    release: str | None = None,
    limit: int = 5,
) -> list[RecordingMatch]:
    """Search MusicBrainz for recordings matching the given criteria."""
    query_parts: list[str] = []
    if artist:
        query_parts.append(f'artist:"{_escape(artist)}"')
    if recording:
        query_parts.append(f'recording:"{_escape(recording)}"')
    if release:
        query_parts.append(f'release:"{_escape(release)}"')

    if not query_parts:
        return []

    query = ' AND '.join(query_parts)
    raw = _rate_limited_get(
        '/recording',
        params={'query': query, 'limit': limit, 'fmt': 'json'},
    )
    data = cast(_MBSearchResult, raw)

    matches: list[RecordingMatch] = []
    for rec in data.get('recordings', []):
        parts: list[str] = []
        for ac in rec.get('artist-credit', []):
            artist_info = ac.get('artist')
            name = ac.get('name', '') or (
                artist_info.get('name', '') if artist_info else ''
            )
            parts.append(name)
            parts.append(ac.get('joinphrase', ''))
        artist_credit = ''.join(parts).strip()

        releases = rec.get('releases', [])
        release_title = releases[0].get('title', '') if releases else ''

        matches.append(
            RecordingMatch(
                mbid=rec.get('id', ''),
                title=rec.get('title', ''),
                artist_credit=artist_credit,
                score=rec.get('score', 0),
                release=release_title,
            )
        )

    return matches


def _track_name_variants(recording: str) -> list[str]:
    """Return the original track name and its normalized form (if different)."""
    normalized = normalize_track_name(recording)
    if normalized != recording and normalized:
        return [recording, normalized]
    return [recording]


def _release_name_variants(release: str) -> list[str]:
    """Return the original release name and its normalized form (if different)."""
    normalized = normalize_release_name(release)
    if normalized != release and normalized:
        return [release, normalized]
    return [release]


def find_best_match(
    artist: str,
    recording: str,
    min_score: int = AUTO_ACCEPT_SCORE,
) -> RecordingMatch | None:
    """Find the best MusicBrainz match above the score threshold.

    Tries the exact track name first, then a normalized version with
    annotations / edit markers / movement markings stripped.
    """
    # Exact search
    results = search_recordings(artist=artist, recording=recording, limit=5)
    above_threshold = [r for r in results if r.score >= min_score]
    if above_threshold:
        return above_threshold[0]

    # Retry with normalized track name
    normalized = normalize_track_name(recording)
    if normalized != recording:
        results = search_recordings(artist=artist, recording=normalized, limit=5)
        above_threshold = [r for r in results if r.score >= min_score]
        if above_threshold:
            return above_threshold[0]

    return None


def find_match_by_track_release(
    recording: str,
    release: str,
    expected_artist: str,
) -> RecordingMatch | None:
    """Fallback: search by track + release, require artist match."""
    expected_lower = expected_artist.lower()
    for release_name in _release_name_variants(release):
        for track_name in _track_name_variants(recording):
            results = search_recordings(
                recording=track_name,
                release=release_name,
                limit=10,
            )
            for r in results:
                if expected_lower in r.artist_credit.lower():
                    return r

    return None


def find_match_by_track_only(
    recording: str,
    expected_artist: str,
    min_score: int = AUTO_ACCEPT_SCORE,
) -> RecordingMatch | None:
    """Last-resort: search by track name only, REQUIRE artist match.

    Only returns a result when the expected artist name appears in the
    artist credit. Never returns a "wrong artist" match.
    """
    expected_lower = expected_artist.lower()
    for track_name in _track_name_variants(recording):
        results = search_recordings(recording=track_name, limit=10)
        for r in results:
            if r.score >= min_score and expected_lower in r.artist_credit.lower():
                return r

    return None


def close_client() -> None:
    """Close the module-level HTTP client."""
    _client.close()


def _escape(s: str) -> str:
    """Escape Lucene special characters in query values."""
    special = r'+-&|!(){}[]^"~*?:\/'
    return ''.join(f'\\{c}' if c in special else c for c in s)

"""MusicBrainz recording search via direct API calls."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, TypedDict, cast

from lb_mapper.lb_client import create_http_client


MB_BASE_URL = 'https://musicbrainz.org/ws/2'
USER_AGENT = 'lb-mapper/0.1.0 (https://github.com/hakula/listenbrainz-auto-mapper)'

# Rate limiting: 1 request per second
_last_request_time: float = 0.0

_client = create_http_client(
    base_url=MB_BASE_URL,
    headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'},
)


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


def close_client() -> None:
    """Close the module-level HTTP client."""
    _client.close()


def _escape(s: str) -> str:
    """Escape Lucene special characters in query values."""
    special = r'+-&|!(){}[]^"~*?:\/'
    return ''.join(f'\\{c}' if c in special else c for c in s)

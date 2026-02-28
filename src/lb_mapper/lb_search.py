"""ListenBrainz Labs recording search via Typesense-backed API."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import cache

import httpx


logger = logging.getLogger(__name__)

LB_LABS_URL = 'https://labs.api.listenbrainz.org'
_MAX_QUERY_LEN = 200

# CJK Unified Ideographs, Katakana, Hiragana, CJK symbols
_CJK_RE = re.compile(r'[\u3000-\u9fff]')


def contains_cjk(text: str) -> bool:
    """Check whether *text* contains any CJK characters."""
    return bool(_CJK_RE.search(text))


@dataclass(frozen=True)
class LBRecordingMatch:
    recording_mbid: str
    recording_name: str
    release_name: str
    release_mbid: str
    artist_credit_name: str
    artist_credit_id: int


@cache
def _get_client() -> httpx.Client:
    return httpx.Client(
        transport=httpx.HTTPTransport(retries=3),
        base_url=LB_LABS_URL,
        headers={'Accept': 'application/json'},
        timeout=30.0,
    )


def search_recording(
    artist: str,
    recording: str,
) -> list[LBRecordingMatch]:
    """Search LB Labs for recordings matching artist + track name.

    Returns an empty list on any HTTP or transport error.
    """
    query = f'{artist} {recording}'.strip()
    if not query:
        return []

    # Typesense returns 500 on very long queries
    if len(query) > _MAX_QUERY_LEN:
        query = query[:_MAX_QUERY_LEN].rsplit(' ', 1)[0]

    try:
        resp = _get_client().post('/recording-search/json', json=[{'query': query}])
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug('LB Labs search failed for query %s: %s', query[:80], exc)
        return []

    return [
        LBRecordingMatch(
            recording_mbid=item.get('recording_mbid', ''),
            recording_name=item.get('recording_name', ''),
            release_name=item.get('release_name', ''),
            release_mbid=item.get('release_mbid', ''),
            artist_credit_name=item.get('artist_credit_name', ''),
            artist_credit_id=item.get('artist_credit_id', 0),
        )
        for item in resp.json()
    ]

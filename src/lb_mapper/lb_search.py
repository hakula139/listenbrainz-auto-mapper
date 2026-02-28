"""ListenBrainz Labs recording search via Typesense-backed API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from lb_mapper.lb_client import create_http_client


logger = logging.getLogger(__name__)

LB_LABS_URL = 'https://labs.api.listenbrainz.org'

_client = create_http_client(
    base_url=LB_LABS_URL,
    headers={'Accept': 'application/json'},
)


@dataclass(frozen=True)
class LBRecordingMatch:
    recording_mbid: str
    recording_name: str
    release_name: str
    release_mbid: str
    artist_credit_name: str
    artist_credit_id: int


_MAX_QUERY_LEN = 200


def search_recording(
    artist: str,
    recording: str,
) -> list[LBRecordingMatch]:
    """Search LB Labs for recordings matching artist + track name.

    The Typesense backend handles fuzzy matching, typo tolerance, and long
    classical titles far better than MusicBrainz Lucene phrase queries.
    """
    query = f'{artist} {recording}'.strip()
    if not query:
        return []

    # Typesense returns 500 on very long queries; truncate to stay safe
    if len(query) > _MAX_QUERY_LEN:
        query = query[:_MAX_QUERY_LEN].rsplit(' ', 1)[0]

    payload: list[dict[str, Any]] = [{'query': query}]
    try:
        resp = _client.post('/recording-search/json', json=payload)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.debug(
            'LB Labs search returned %s for query: %s',
            e.response.status_code,
            query[:80],
        )
        return []

    results: list[LBRecordingMatch] = []
    for item in resp.json():
        results.append(
            LBRecordingMatch(
                recording_mbid=item.get('recording_mbid', ''),
                recording_name=item.get('recording_name', ''),
                release_name=item.get('release_name', ''),
                release_mbid=item.get('release_mbid', ''),
                artist_credit_name=item.get('artist_credit_name', ''),
                artist_credit_id=item.get('artist_credit_id', 0),
            )
        )
    return results


def close_client() -> None:
    """Close the module-level HTTP client."""
    _client.close()

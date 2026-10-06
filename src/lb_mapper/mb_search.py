"""MusicBrainz recording search and identity lookup."""

from __future__ import annotations

import atexit
import time
from functools import cache
from typing import Any

import httpx

from lb_mapper import USER_AGENT


@cache
def _get_client() -> httpx.Client:
    client = httpx.Client(
        transport=httpx.HTTPTransport(retries=3),
        base_url='https://musicbrainz.org/ws/2',
        headers={'User-Agent': USER_AGENT},
        timeout=30.0,
    )
    atexit.register(client.close)
    return client


def _request(path: str, **params: str | int) -> dict[str, Any]:
    time.sleep(1.1)
    resp = _get_client().get(path, params={'fmt': 'json', **params})
    resp.raise_for_status()
    data: Any = resp.json()
    if not isinstance(data, dict):
        raise ValueError('MusicBrainz returned a malformed response')
    return data


def recording_query(artist: str, track: str) -> str:
    def quote(text: str) -> str:
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'

    return f'artist:{quote(artist)} AND recording:{quote(track)}'


def search_recordings(query: str) -> list[dict[str, Any]]:
    """Run a Lucene query. One process must own MusicBrainz calls for a run."""
    if not query.strip():
        raise ValueError('MusicBrainz query must not be empty')
    data = _request('/recording/', query=query, limit=25)
    results: Any = data['recordings']
    if not isinstance(results, list) or any(
        not isinstance(item, dict) or not item.get('id') for item in results
    ):
        raise ValueError('MusicBrainz returned malformed recording candidates')
    return results


def lookup_recording(mbid: str) -> dict[str, Any]:
    """Fetch artist credits, releases, ISRCs, and work relationships."""
    data = _request(f'/recording/{mbid}', inc='artist-credits+releases+isrcs+work-rels')
    if data['id'] != mbid:
        raise ValueError('MusicBrainz returned a different recording ID')
    return data

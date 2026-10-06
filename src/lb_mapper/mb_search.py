"""MusicBrainz recording search and identity lookup."""

from __future__ import annotations

import atexit
import time
from functools import cache
from typing import Any

import httpx

from lb_mapper import USER_AGENT
from lb_mapper.validation import uuid_string


_MAX_ATTEMPTS = 3


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


def _request(path: str, **params: str | int) -> tuple[httpx.Response, dict[str, Any]]:
    client = _get_client()
    request = client.build_request('GET', path, params={'fmt': 'json', **params})
    redirects = 0
    failures = 0

    while True:
        time.sleep(1.1)

        try:
            resp = client.send(request, follow_redirects=False)
        except httpx.TransportError:
            failures += 1
            if failures >= _MAX_ATTEMPTS:
                raise
            time.sleep(failures)
            continue

        if resp.status_code in (500, 502, 503, 504) and failures < _MAX_ATTEMPTS - 1:
            failures += 1
            time.sleep(failures)
            continue

        if resp.next_request is None:
            break

        if redirects >= client.max_redirects:
            raise httpx.TooManyRedirects(
                'MusicBrainz redirect limit exceeded', request=request
            )
        if (
            resp.next_request.url.host != client.base_url.host
            or resp.next_request.url.scheme != client.base_url.scheme
        ):
            raise ValueError('MusicBrainz redirect leaves the recording service')

        request = client.build_request(
            'GET', resp.next_request.url.copy_merge_params({'fmt': 'json', **params})
        )
        redirects += 1
        failures = 0

    resp.raise_for_status()

    data: Any = resp.json()
    if not isinstance(data, dict):
        raise ValueError('MusicBrainz returned a malformed response')

    return resp, data


def recording_query(artist: str, track: str) -> str:
    def quote(text: str) -> str:
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'

    return f'artist:{quote(artist)} AND recording:{quote(track)}'


def search_recordings(query: str, offset: int = 0) -> dict[str, Any]:
    """Return a Lucene result page, including total count and offset."""
    if not query.strip():
        raise ValueError('MusicBrainz query must not be empty')
    if type(offset) is not int or offset < 0:
        raise ValueError('MusicBrainz offset must be a non-negative integer')

    _, data = _request('/recording/', query=query, limit=100, offset=offset)
    results: Any = data.get('recordings')
    if not isinstance(results, list) or any(
        not isinstance(item, dict) or not item.get('id') for item in results
    ):
        raise ValueError('MusicBrainz returned malformed recording candidates')
    for item in results:
        uuid_string(item['id'])
    count = data.get('count')
    if (
        type(count) is not int
        or count < 0
        or type(data.get('offset')) is not int
        or data['offset'] != offset
        or (results and offset + len(results) > count)
        or (not results and offset < count)
    ):
        raise ValueError('MusicBrainz returned malformed pagination metadata')

    return data


def validate_recording(data: Any) -> dict[str, Any]:
    """Require canonical identity and the requested metadata containers."""
    if not isinstance(data, dict):
        raise ValueError('MusicBrainz recording must be an object')

    uuid_string(data.get('id'))
    if not isinstance(data.get('title'), str) or not data['title'].strip():
        raise ValueError('MusicBrainz recording requires a title')
    if type(data.get('video')) is not bool:
        raise ValueError('MusicBrainz recording requires its video flag')

    for key in ('artist-credit', 'releases', 'isrcs', 'relations'):
        if not isinstance(data.get(key), list):
            raise ValueError(f'MusicBrainz recording requires {key} metadata')

    if not data['artist-credit'] or any(
        not isinstance(credit, dict)
        or not isinstance(credit.get('name'), str)
        or not credit['name'].strip()
        for credit in data['artist-credit']
    ):
        raise ValueError('MusicBrainz returned malformed recording credits')

    return data


def lookup_recording(mbid: str) -> dict[str, Any]:
    """Fetch artist credits, releases, ISRCs, and work relationships."""
    resp, data = _request(
        f'/recording/{uuid_string(mbid)}', inc='artist-credits+releases+isrcs+work-rels'
    )
    validate_recording(data)
    canonical_mbid = resp.url.path.rstrip('/').rsplit('/', 1)[-1]
    if data['id'] != canonical_mbid:
        raise ValueError('MusicBrainz returned a different recording ID')
    return data

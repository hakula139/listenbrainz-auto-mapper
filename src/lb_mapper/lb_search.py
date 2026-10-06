"""ListenBrainz Labs recording search via Typesense-backed API."""

from __future__ import annotations

import atexit
import re
import time
from functools import cache
from typing import Any

import httpx

from lb_mapper import USER_AGENT


__all__ = ['contains_cjk', 'search_recording']

LB_LABS_URL = 'https://labs.api.listenbrainz.org'
_MAX_QUERY_LEN = 200

# CJK ideographs, kana, bopomofo, Hangul, and halfwidth katakana
_CJK_RE = re.compile(
    r'[\u3040-\u30ff\u3100-\u312f\u31a0-\u31bf\u31f0-\u31ff'
    r'\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff'
    r'\uff66-\uff9d\U00020000-\U0003134f]'
)


def contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


@cache
def _get_client() -> httpx.Client:
    client = httpx.Client(
        transport=httpx.HTTPTransport(retries=3),
        base_url=LB_LABS_URL,
        headers={'Accept': 'application/json', 'User-Agent': USER_AGENT},
        timeout=30.0,
    )
    atexit.register(client.close)
    return client


def search_recording(artist: str, recording: str) -> list[dict[str, Any]]:
    """Return raw candidates. HTTP and malformed-response errors propagate."""
    query = f'{artist} {recording}'.strip()
    if not query or len(query) > _MAX_QUERY_LEN:
        raise ValueError('LB Labs query must contain 1 to 200 characters')

    time.sleep(1.1)
    resp = _get_client().post('/recording-search/json', json=[{'query': query}])
    resp.raise_for_status()
    results: Any = resp.json()
    if not isinstance(results, list) or any(
        not isinstance(item, dict) or not item.get('recording_mbid') for item in results
    ):
        raise ValueError('LB Labs returned malformed recording candidates')
    return results

"""ListenBrainz API client: fetch listens, submit mappings, delete listens."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx


BASE_URL = 'https://api.listenbrainz.org'
_API_PAGE_LIMIT = 100
_MAX_RETRIES = 3


@dataclass(frozen=True)
class Listen:
    """A single listen from ListenBrainz."""

    listened_at: int
    recording_msid: str
    artist_name: str
    track_name: str
    release_name: str
    mbid_mapping: dict[str, Any] | None

    @property
    def is_linked(self) -> bool:
        return self.mbid_mapping is not None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Listen:
        tm = data['track_metadata']
        additional = tm.get('additional_info', {})
        return cls(
            listened_at=data['listened_at'],
            recording_msid=additional.get(
                'recording_msid',
                data.get('recording_msid', ''),
            ),
            artist_name=tm.get('artist_name', ''),
            track_name=tm.get('track_name', ''),
            release_name=tm.get('release_name', ''),
            mbid_mapping=tm.get('mbid_mapping'),
        )


class ListenBrainzClient:
    def __init__(self, token: str) -> None:
        self._client = httpx.Client(
            transport=httpx.HTTPTransport(retries=3),
            base_url=BASE_URL,
            headers={'Authorization': f'Token {token}'},
            timeout=30.0,
        )

    def __enter__(self) -> ListenBrainzClient:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object,
    ) -> None:
        self.close()

    def iter_listens(self, user: str, max_ts: int | None = None) -> Iterator[Listen]:
        """Yield listens in reverse chronological order.

        Paginates through the API transparently, deduplicating across page
        boundaries where listens share the same ``listened_at`` timestamp.
        """
        seen: set[tuple[int, str]] = set()
        while True:
            params: dict[str, Any] = {'count': _API_PAGE_LIMIT}
            if max_ts is not None:
                params['max_ts'] = max_ts

            resp = self._request('GET', f'/1/user/{user}/listens', params=params)
            listens_data = resp.json()['payload']['listens']
            if not listens_data:
                return

            added = False
            for item in listens_data:
                listen = Listen.from_api(item)
                key = (listen.listened_at, listen.recording_msid)
                if key not in seen:
                    seen.add(key)
                    yield listen
                    added = True

            if not added:
                return

            # Include the boundary timestamp so we don't skip listens
            # sharing the same second. The ``seen`` set filters duplicates.
            max_ts = listens_data[-1]['listened_at'] + 1

    def fetch_listens(
        self, user: str, count: int = 50, max_ts: int | None = None
    ) -> list[Listen]:
        """Fetch *count* recent listens."""
        result: list[Listen] = []
        for listen in self.iter_listens(user, max_ts):
            result.append(listen)
            if len(result) >= count:
                break
        return result

    def submit_mapping(self, recording_msid: str, recording_mbid: str) -> None:
        self._request(
            'POST',
            '/1/metadata/submit_manual_mapping/',
            json={
                'recording_msid': recording_msid,
                'recording_mbid': recording_mbid,
            },
        )

    def delete_listen(self, listened_at: int, recording_msid: str) -> None:
        self._request(
            'POST',
            '/1/delete-listen',
            json={
                'listened_at': listened_at,
                'recording_msid': recording_msid,
            },
        )

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Make an HTTP request with rate-limit awareness and 429 retry."""
        retries_left = _MAX_RETRIES
        while True:
            resp = self._client.request(method, url, **kwargs)
            retries_left -= 1
            if resp.status_code != 429 or retries_left <= 0:
                break
            self._sleep_for_reset(resp)
        resp.raise_for_status()
        self._sleep_if_near_limit(resp)
        return resp

    def _sleep_for_reset(self, resp: httpx.Response) -> None:
        """Sleep until the rate-limit window resets."""
        try:
            reset_in = float(resp.headers.get('X-RateLimit-Reset-In', '1'))
        except ValueError:
            reset_in = 1.0
        time.sleep(max(reset_in, 0.1))

    def _sleep_if_near_limit(self, resp: httpx.Response) -> None:
        """Preemptively sleep when rate-limit headroom is low."""
        remaining = resp.headers.get('X-RateLimit-Remaining')
        if remaining is None:
            return
        try:
            if int(remaining) > 1:
                return
        except ValueError:
            return
        self._sleep_for_reset(resp)

    def close(self) -> None:
        self._client.close()

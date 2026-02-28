"""ListenBrainz API client: fetch listens, submit mappings, delete listens."""

from __future__ import annotations

import time
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

    def fetch_listens(
        self, user: str, count: int = 50, max_ts: int | None = None
    ) -> list[Listen]:
        """Fetch recent listens, paginating transparently.

        Deduplicates across page boundaries to handle listens that share
        the same ``listened_at`` timestamp.
        """
        all_listens: list[Listen] = []
        seen: set[tuple[int, str]] = set()
        while len(all_listens) < count:
            params: dict[str, Any] = {'count': _API_PAGE_LIMIT}
            if max_ts is not None:
                params['max_ts'] = max_ts

            resp = self._request('GET', f'/1/user/{user}/listens', params=params)
            listens_data = resp.json()['payload']['listens']
            if not listens_data:
                break

            added = 0
            for item in listens_data:
                listen = Listen.from_api(item)
                key = (listen.listened_at, listen.recording_msid)
                if key not in seen:
                    seen.add(key)
                    all_listens.append(listen)
                    added += 1

            if added == 0:
                break

            # Include the boundary timestamp in the next query to avoid
            # skipping listens that share the same second as the last item.
            # Duplicates are filtered by the ``seen`` set above.
            max_ts = all_listens[-1].listened_at + 1

        return all_listens[:count]

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

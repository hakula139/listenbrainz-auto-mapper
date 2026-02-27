"""ListenBrainz API client: fetch listens and submit manual MBID mappings."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx


BASE_URL = 'https://api.listenbrainz.org'


def create_http_client(**kwargs: Any) -> httpx.Client:
    """Create an httpx client with retry transport and SSL verification disabled."""
    transport = httpx.HTTPTransport(retries=3)
    defaults: dict[str, Any] = {
        'transport': transport,
        'verify': False,
        'timeout': 30.0,
    }
    defaults.update(kwargs)
    return httpx.Client(**defaults)


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
        self._token = token
        self._client = create_http_client(
            base_url=BASE_URL,
            headers={'Authorization': f'Token {token}'},
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
        params: dict[str, Any] = {'count': count}
        if max_ts is not None:
            params['max_ts'] = max_ts

        resp = self._client.get(f'/1/user/{user}/listens', params=params)
        self._handle_rate_limit(resp)
        resp.raise_for_status()

        listens_data = resp.json()['payload']['listens']
        return [Listen.from_api(item) for item in listens_data]

    def submit_mapping(self, recording_msid: str, recording_mbid: str) -> None:
        resp = self._client.post(
            '/1/metadata/submit_manual_mapping/',
            json={
                'recording_msid': recording_msid,
                'recording_mbid': recording_mbid,
            },
        )
        self._handle_rate_limit(resp)
        resp.raise_for_status()

    def _handle_rate_limit(self, resp: httpx.Response) -> None:
        remaining = resp.headers.get('X-RateLimit-Remaining')
        if remaining is None:
            return
        try:
            remaining_int = int(remaining)
        except ValueError:
            return
        if remaining_int <= 1:
            try:
                reset_in = float(resp.headers.get('X-RateLimit-Reset-In', '1'))
            except ValueError:
                reset_in = 1.0
            time.sleep(reset_in)

    def close(self) -> None:
        self._client.close()

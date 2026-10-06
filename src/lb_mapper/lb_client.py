"""ListenBrainz API client: fetch listens, submit mappings, delete listens."""

from __future__ import annotations

import time
import types
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx

from lb_mapper import USER_AGENT
from lb_mapper.artifacts import atomic_path
from lb_mapper.validation import uuid_string


__all__ = ['Listen', 'ListenBrainzClient']

BASE_URL = 'https://api.listenbrainz.org'
_API_PAGE_LIMIT = 100
_MAX_PAGE_LIMIT = 1000
_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Listen:
    """A single listen from ListenBrainz."""

    listened_at: int
    recording_msid: str
    artist_name: str
    track_name: str
    release_name: str
    mbid_mapping: dict[str, Any] | None
    additional_info: dict[str, Any] = field(default_factory=dict)

    @property
    def recording_mbid(self) -> str | None:
        return self.additional_info.get('recording_mbid') or (
            (self.mbid_mapping or {}).get('recording_mbid')
        )

    @property
    def is_linked(self) -> bool:
        return bool(self.recording_mbid)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Listen:
        if not isinstance(data, dict):
            raise ValueError('ListenBrainz listen must be an object')

        tm = data.get('track_metadata')
        timestamp = data.get('listened_at')
        if not isinstance(tm, dict) or type(timestamp) is not int or timestamp < 0:
            raise ValueError('ListenBrainz returned malformed listen metadata')

        for key in ('artist_name', 'track_name'):
            if not isinstance(tm.get(key), str) or not tm[key].strip():
                raise ValueError(f'ListenBrainz listen requires {key}')

        release = tm.get('release_name')
        if release is not None and not isinstance(release, str):
            raise ValueError('ListenBrainz release_name must be a string or null')

        additional = tm.get('additional_info')
        mapping = tm.get('mbid_mapping')
        if additional is None:
            additional = {}
        if not isinstance(additional, dict) or (
            mapping is not None and not isinstance(mapping, dict)
        ):
            raise ValueError('ListenBrainz returned malformed mapping metadata')

        for metadata in (additional, mapping or {}):
            if metadata.get('recording_mbid') is not None:
                uuid_string(metadata['recording_mbid'])

        msid = data.get('recording_msid') or additional.get('recording_msid', '')
        if msid:
            msid = uuid_string(msid)

        return cls(
            listened_at=timestamp,
            recording_msid=msid,
            artist_name=tm['artist_name'],
            track_name=tm['track_name'],
            release_name=release or '',
            mbid_mapping=mapping,
            additional_info=additional,
        )


class ListenBrainzClient:
    def __init__(self, token: str) -> None:
        self._client = httpx.Client(
            transport=httpx.HTTPTransport(retries=3),
            base_url=BASE_URL,
            headers={'Authorization': f'Token {token}', 'User-Agent': USER_AGENT},
            timeout=30.0,
        )

    def __enter__(self) -> ListenBrainzClient:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: types.TracebackType | None,
    ) -> None:
        self.close()

    def iter_listens(self, user: str, max_ts: int | None = None) -> Iterator[Listen]:
        """Yield listens in reverse chronological order.

        Paginates through the API transparently, deduplicating across page
        boundaries where listens share the same ``listened_at`` timestamp.
        Raises RuntimeError if a timestamp saturates the API's largest page.
        """
        seen: set[tuple[int, str]] = set()
        page_limit = _API_PAGE_LIMIT

        while True:
            params: dict[str, Any] = {'count': page_limit}
            if max_ts is not None:
                params['max_ts'] = max_ts

            listens = self._listen_page(user, **params)
            if not listens:
                return

            added = False
            for listen in listens:
                key = (listen.listened_at, listen.recording_msid)
                if key not in seen:
                    seen.add(key)
                    yield listen
                    added = True

            if len(listens) < page_limit:
                return

            if not added:
                if page_limit == _MAX_PAGE_LIMIT:
                    raise RuntimeError(
                        'A timestamp fills the maximum ListenBrainz page. '
                        'Use a listen export to avoid omitting history.'
                    )
                page_limit = min(page_limit * 2, _MAX_PAGE_LIMIT)
                continue

            # Prune: only entries at the boundary timestamp can reappear
            # on the next page, so discard the rest to bound memory usage.
            boundary_ts = listens[-1].listened_at
            seen = {k for k in seen if k[0] == boundary_ts}

            # max_ts is exclusive, so +1 re-requests the boundary second.
            # The seen set deduplicates entries already yielded.
            max_ts = boundary_ts + 1

    def fetch_listens(
        self, user: str, count: int = 50, max_ts: int | None = None
    ) -> list[Listen]:
        """Fetch *count* recent listens."""
        if count < 1:
            raise ValueError('count must be positive')

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

    def validate_token(self, user: str) -> None:
        data = self._request('GET', '/1/validate-token').json()
        if not isinstance(data, dict) or type(data.get('valid')) is not bool:
            raise ValueError('ListenBrainz returned malformed token validation')
        if not data['valid'] or data.get('user_name') != user:
            raise ValueError('LB_TOKEN does not belong to the requested LB_USER')

    def get_manual_mapping(self, recording_msid: str) -> str | None:
        try:
            resp = self._request(
                'GET',
                '/1/metadata/get_manual_mapping/',
                params={'recording_msid': recording_msid},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

        data = resp.json()
        if not isinstance(data, dict) or not isinstance(data.get('mapping'), dict):
            raise ValueError('ListenBrainz returned malformed manual mapping')

        return uuid_string(data['mapping'].get('recording_mbid'))

    def _listen_page(self, user: str, **params: Any) -> list[Listen]:
        data = self._request('GET', f'/1/user/{user}/listens', params=params).json()
        if not isinstance(data, dict) or not isinstance(data.get('payload'), dict):
            raise ValueError('ListenBrainz returned malformed listen response')

        items = data['payload'].get('listens')
        if not isinstance(items, list):
            raise ValueError('ListenBrainz listens must be an array')

        listens = [Listen.from_api(item) for item in items]
        if any(not listen.recording_msid for listen in listens):
            raise ValueError('ListenBrainz listen page requires recording MSIDs')
        if any(a.listened_at < b.listened_at for a, b in pairwise(listens)):
            raise ValueError('ListenBrainz listens are not newest first')
        if 'max_ts' in params and any(
            listen.listened_at >= params['max_ts'] for listen in listens
        ):
            raise ValueError('ListenBrainz listen exceeds the requested time boundary')

        return listens

    def get_listen(
        self, user: str, listened_at: int, recording_msid: str
    ) -> Listen | None:
        """Find one occurrence, detecting a saturated timestamp page."""
        for count in (100, _MAX_PAGE_LIMIT):
            listens = self._listen_page(user, count=count, max_ts=listened_at + 1)

            for listen in listens:
                if (listen.listened_at, listen.recording_msid) == (
                    listened_at,
                    recording_msid,
                ):
                    return listen

            if len(listens) < count or listens[-1].listened_at < listened_at:
                return None

        raise RuntimeError('Cannot resolve this occurrence in a saturated page')

    def list_exports(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self._request('GET', '/1/export/list').json()
        return data

    def request_export(self) -> dict[str, Any]:
        data: dict[str, Any] = self._request('POST', '/1/export/').json()
        return data

    def get_export(self, export_id: int) -> dict[str, Any]:
        data: dict[str, Any] = self._request('GET', f'/1/export/{export_id}').json()
        return data

    def download_export(self, export_id: int, path: Path) -> None:
        time.sleep(1.1)
        with (
            atomic_path(path) as temporary,
            self._client.stream(
                'GET',
                f'/1/export/{export_id}/download',
                timeout=120.0,
                follow_redirects=True,
            ) as resp,
        ):
            resp.raise_for_status()
            with temporary.open('wb') as stream:
                for chunk in resp.iter_bytes():
                    stream.write(chunk)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Pace requests and retry throttling or transient failures of safe reads."""
        retries_left = _MAX_ATTEMPTS

        while True:
            retries_left -= 1
            time.sleep(1.1)

            try:
                resp = self._client.request(method, url, **kwargs)
            except httpx.TransportError:
                if method != 'GET' or retries_left <= 0:
                    raise
                time.sleep(_MAX_ATTEMPTS - retries_left)
                continue

            if resp.status_code == 429 and retries_left > 0:
                self._sleep_for_reset(resp)
                continue
            if (
                method == 'GET'
                and resp.status_code in (500, 502, 503, 504)
                and retries_left > 0
            ):
                time.sleep(_MAX_ATTEMPTS - retries_left)
                continue

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

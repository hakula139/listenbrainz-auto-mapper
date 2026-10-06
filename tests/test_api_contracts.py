"""Reject malformed successful responses before drawing account-state conclusions."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from lb_mapper import mb_search
from lb_mapper.lb_client import Listen, ListenBrainzClient
from tests.fixtures import MBID, MSID, TIMESTAMP, api_listen, recording


def test_failed_export_download_preserves_archive_and_staging_name_collision(tmp_path):

    class InterruptedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'partial archive'
            raise httpx.ReadError('interrupted')

    def respond(request):
        return httpx.Response(200, stream=InterruptedStream())

    root = tmp_path
    archive = root / 'history.zip'
    collision = root / 'history.zip.tmp'
    archive.write_bytes(b'previous archive')
    collision.write_bytes(b'another input')

    with (
        ListenBrainzClient('offline') as lb,
        httpx.Client(
            base_url='https://test', transport=httpx.MockTransport(respond)
        ) as client,
        patch.object(lb, '_client', client),
        patch('lb_mapper.lb_client.time.sleep'),
        pytest.raises(httpx.ReadError),
    ):
        lb.download_export(7, archive)

    assert archive.read_bytes() == b'previous archive'
    assert collision.read_bytes() == b'another input'
    assert set(root.iterdir()) == {archive, collision}


@pytest.mark.parametrize(
    'payload',
    (
        [],
        {},
        {'payload': None},
        {'payload': {}},
        {'payload': {'listens': {}}},
        {'payload': {'listens': None}},
        {'payload': {'listens': [api_listen(TIMESTAMP, 1), {}]}},
        {
            'payload': {
                'listens': [
                    {
                        'listened_at': TIMESTAMP,
                        'track_metadata': {'artist_name': 'A', 'track_name': 'B'},
                    }
                ]
            }
        },
    ),
)
def test_malformed_pages_cannot_establish_absence_or_exhausted_history(payload):
    with ListenBrainzClient('offline') as lb:
        response = httpx.Response(200, json=payload)
        with patch.object(lb, '_request', return_value=response):
            with pytest.raises(ValueError):
                list(lb.iter_listens('user'))
            with pytest.raises(ValueError):
                lb.get_listen('user', TIMESTAMP, MSID)


def test_invalid_required_listen_fields_fail_at_the_boundary():
    valid = api_listen(TIMESTAMP, 1)
    metadata = valid['track_metadata']

    for change in (
        {'listened_at': True},
        {'listened_at': -1},
        {'recording_msid': 'invalid'},
        {'track_metadata': []},
        {'track_metadata': {**metadata, 'artist_name': None}},
        {'track_metadata': {**metadata, 'track_name': ''}},
        {'track_metadata': {**metadata, 'additional_info': []}},
        {'track_metadata': {**metadata, 'mbid_mapping': []}},
        {'track_metadata': {**metadata, 'mbid_mapping': {'recording_mbid': 'invalid'}}},
    ):
        with pytest.raises(ValueError):
            Listen.from_api({**valid, **change})


def test_only_a_404_establishes_missing_manual_mapping():
    with ListenBrainzClient('offline') as lb:
        for payload in (
            {},
            {'mapping': None},
            {'mapping': {}},
            {'mapping': {'recording_mbid': None}},
            {'mapping': {'recording_mbid': 'invalid'}},
        ):
            response = httpx.Response(200, json=payload)
            with (
                patch.object(lb, '_request', return_value=response),
                pytest.raises(ValueError),
            ):
                lb.get_manual_mapping(MSID)
        response = httpx.Response(404, request=httpx.Request('GET', 'https://test'))
        with patch.object(
            lb,
            '_request',
            side_effect=httpx.HTTPStatusError(
                'missing', request=response.request, response=response
            ),
        ):
            assert lb.get_manual_mapping(MSID) is None
        response = httpx.Response(200, json={'mapping': {'recording_mbid': MBID}})
        with patch.object(lb, '_request', return_value=response):
            assert lb.get_manual_mapping(MSID) == MBID


@pytest.mark.parametrize(
    'items',
    (
        [api_listen(TIMESTAMP - 1, 1), api_listen(TIMESTAMP, 2)],
        [api_listen(TIMESTAMP + 1, 1)],
    ),
)
def test_out_of_order_and_out_of_range_pages_cannot_prove_absence(items):
    with ListenBrainzClient('offline') as lb:
        response = httpx.Response(200, json={'payload': {'listens': items}})
        with (
            patch.object(lb, '_request', return_value=response),
            pytest.raises(ValueError),
        ):
            lb.get_listen('user', TIMESTAMP, MSID)


def test_truthy_token_validation_values_do_not_authorize_writes():
    with ListenBrainzClient('offline') as lb:
        response = httpx.Response(200, json={'valid': 'false', 'user_name': 'user'})
        with (
            patch.object(lb, '_request', return_value=response),
            pytest.raises(ValueError, match='malformed token'),
        ):
            lb.validate_token('user')


@pytest.mark.parametrize(
    'page',
    (
        {'recordings': [], 'count': -1, 'offset': 0},
        {'recordings': [], 'count': True, 'offset': 0},
        {'recordings': [], 'count': 0, 'offset': False},
        {'recordings': [], 'count': 1, 'offset': 0},
        {'recordings': [recording()], 'count': 0, 'offset': 0},
        {'recordings': [{'id': 'invalid'}], 'count': 1, 'offset': 0},
    ),
)
def test_bad_pagination_does_not_become_a_successful_search(page):
    with (
        patch.object(mb_search, '_request', return_value=(None, page)),
        pytest.raises(ValueError),
    ):
        mb_search.search_recordings('Title')


@pytest.mark.parametrize(
    'key', ('title', 'video', 'artist-credit', 'releases', 'isrcs', 'relations')
)
def test_lookup_requires_identity_and_requested_metadata(key):
    payload = recording()
    del payload[key]

    with pytest.raises(ValueError):
        mb_search.validate_recording(payload)

    assert mb_search.validate_recording(recording()) == recording()

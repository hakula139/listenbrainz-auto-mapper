"""Reject malformed successful responses before drawing account-state conclusions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from lb_mapper import mb_search
from lb_mapper.lb_client import Listen, ListenBrainzClient
from tests.fixtures import MBID, MSID, TIMESTAMP, api_listen, recording


class ListenBrainzContractTests(unittest.TestCase):
    def test_failed_export_download_preserves_archive_and_staging_name_collision(self):
        class InterruptedStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b'partial archive'
                raise httpx.ReadError('interrupted')

        def respond(request):
            return httpx.Response(200, stream=InterruptedStream())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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
                self.assertRaises(httpx.ReadError),
            ):
                lb.download_export(7, archive)

            self.assertEqual(archive.read_bytes(), b'previous archive')
            self.assertEqual(collision.read_bytes(), b'another input')
            self.assertEqual(set(root.iterdir()), {archive, collision})

    def test_malformed_pages_cannot_establish_absence_or_exhausted_history(self):
        for payload in (
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
        ):
            with self.subTest(payload=payload), ListenBrainzClient('offline') as lb:
                response = httpx.Response(200, json=payload)
                with patch.object(lb, '_request', return_value=response):
                    with self.assertRaises(ValueError):
                        list(lb.iter_listens('user'))
                    with self.assertRaises(ValueError):
                        lb.get_listen('user', TIMESTAMP, MSID)

    def test_invalid_required_listen_fields_fail_at_the_boundary(self):
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
            {
                'track_metadata': {
                    **metadata,
                    'mbid_mapping': {'recording_mbid': 'invalid'},
                }
            },
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                Listen.from_api({**valid, **change})

    def test_only_a_404_establishes_missing_manual_mapping(self):
        with ListenBrainzClient('offline') as lb:
            for payload in (
                {},
                {'mapping': None},
                {'mapping': {}},
                {'mapping': {'recording_mbid': None}},
                {'mapping': {'recording_mbid': 'invalid'}},
            ):
                with self.subTest(payload=payload):
                    response = httpx.Response(200, json=payload)
                    with (
                        patch.object(lb, '_request', return_value=response),
                        self.assertRaises(ValueError),
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
                self.assertIsNone(lb.get_manual_mapping(MSID))

            response = httpx.Response(200, json={'mapping': {'recording_mbid': MBID}})
            with patch.object(lb, '_request', return_value=response):
                self.assertEqual(lb.get_manual_mapping(MSID), MBID)

    def test_out_of_order_and_out_of_range_pages_cannot_prove_absence(self):
        for items in (
            [api_listen(TIMESTAMP - 1, 1), api_listen(TIMESTAMP, 2)],
            [api_listen(TIMESTAMP + 1, 1)],
        ):
            with self.subTest(items=items), ListenBrainzClient('offline') as lb:
                response = httpx.Response(200, json={'payload': {'listens': items}})
                with (
                    patch.object(lb, '_request', return_value=response),
                    self.assertRaises(ValueError),
                ):
                    lb.get_listen('user', TIMESTAMP, MSID)

    def test_truthy_token_validation_values_do_not_authorize_writes(self):
        with ListenBrainzClient('offline') as lb:
            response = httpx.Response(200, json={'valid': 'false', 'user_name': 'user'})
            with (
                patch.object(lb, '_request', return_value=response),
                self.assertRaisesRegex(ValueError, 'malformed token'),
            ):
                lb.validate_token('user')


class MusicBrainzContractTests(unittest.TestCase):
    def test_bad_pagination_does_not_become_a_successful_search(self):
        for page in (
            {'recordings': [], 'count': -1, 'offset': 0},
            {'recordings': [], 'count': True, 'offset': 0},
            {'recordings': [], 'count': 0, 'offset': False},
            {'recordings': [], 'count': 1, 'offset': 0},
            {'recordings': [recording()], 'count': 0, 'offset': 0},
            {'recordings': [{'id': 'invalid'}], 'count': 1, 'offset': 0},
        ):
            with (
                self.subTest(page=page),
                patch.object(mb_search, '_request', return_value=(None, page)),
                self.assertRaises(ValueError),
            ):
                mb_search.search_recordings('Title')

    def test_lookup_requires_identity_and_requested_metadata(self):
        for key in (
            'title',
            'video',
            'artist-credit',
            'releases',
            'isrcs',
            'relations',
        ):
            payload = recording()
            del payload[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                mb_search.validate_recording(payload)

        self.assertEqual(mb_search.validate_recording(recording()), recording())


if __name__ == '__main__':
    unittest.main()

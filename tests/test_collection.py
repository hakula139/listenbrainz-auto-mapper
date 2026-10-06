"""Behavioral regressions for mapper collection contracts."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import httpx

from lb_mapper.cli import fetch_listens
from lb_mapper.lb_client import Listen, ListenBrainzClient
from tests.fixtures import (
    MBID,
    MSID,
    OTHER_MBID,
    OTHER_MSID,
    TIMESTAMP,
    api_listen,
)


class CollectionTests(unittest.TestCase):
    def test_requested_count_skips_linked_and_keeps_repeated_occurrences(self):
        seen = []
        rows = [
            Listen(
                TIMESTAMP,
                OTHER_MSID,
                'Linked',
                'Track',
                '',
                None,
                {'recording_mbid': MBID},
            ),
            Listen(TIMESTAMP - 1, MSID, 'A', 'B', 'Album', None),
            Listen(TIMESTAMP - 2, MSID, 'A', 'B', 'Album', None),
            Listen(TIMESTAMP - 3, OTHER_MSID, 'Older', 'Track', '', None),
        ]

        def history():
            for row in rows:
                seen.append(row.listened_at)
                yield row

        lb = MagicMock()
        lb.__enter__.return_value = lb
        lb.iter_listens.return_value = history()
        output = io.StringIO()
        with (
            patch('sys.argv', ['fetch_listens', '2']),
            patch.object(fetch_listens, 'load_dotenv'),
            patch.object(fetch_listens, 'require_env', side_effect=['user', 'token']),
            patch.object(fetch_listens, 'ListenBrainzClient', return_value=lb),
            redirect_stdout(output),
        ):
            fetch_listens.main()

        snapshot = json.loads(output.getvalue())
        self.assertEqual(seen, [TIMESTAMP, TIMESTAMP - 1, TIMESTAMP - 2])
        self.assertEqual(snapshot['linked'], 1)
        self.assertEqual(snapshot['total'], 3)
        self.assertEqual(
            [
                (row['listened_at'], row['recording_msid'])
                for row in snapshot['unlinked']
            ],
            [(TIMESTAMP - 1, MSID), (TIMESTAMP - 2, MSID)],
        )

    def test_timestamp_saturation_expands_page_and_reaches_older_history(self):
        history = [api_listen(TIMESTAMP, i) for i in range(120)]
        history.append(api_listen(TIMESTAMP - 1, 999))

        def respond(method, url, **kwargs):
            params = kwargs['params']
            eligible = [
                item
                for item in history
                if item['listened_at'] < params.get('max_ts', TIMESTAMP + 1)
            ]
            return httpx.Response(
                200, json={'payload': {'listens': eligible[: params['count']]}}
            )

        with (
            ListenBrainzClient('offline') as client,
            patch.object(client, '_request', side_effect=respond),
        ):
            actual = list(client.iter_listens('user'))
        self.assertEqual(
            [item.recording_msid for item in actual],
            [item['recording_msid'] for item in history],
        )

    def test_maximum_saturated_timestamp_fails_without_claiming_exhaustion(self):
        page = [api_listen(TIMESTAMP, i) for i in range(1000)]

        def respond(method, url, **kwargs):
            return httpx.Response(
                200, json={'payload': {'listens': page[: kwargs['params']['count']]}}
            )

        with (
            ListenBrainzClient('offline') as client,
            patch.object(client, '_request', side_effect=respond),
            self.assertRaisesRegex(RuntimeError, 'export'),
        ):
            list(client.iter_listens('user'))

    def test_empty_mapping_is_unlinked_and_submitted_mbid_is_preserved(self):
        item = api_listen(TIMESTAMP, 1)
        item['track_metadata']['mbid_mapping'] = {}
        item['track_metadata']['additional_info'] = None
        self.assertFalse(Listen.from_api(item).is_linked)
        info = {'recording_mbid': MBID, 'duration_ms': 240000, 'isrc': 'TEST'}
        item['track_metadata']['additional_info'] = info
        parsed = Listen.from_api(item)
        self.assertEqual(parsed.recording_mbid, MBID)
        self.assertTrue(parsed.is_linked)
        self.assertEqual(parsed.additional_info, info)

    def test_top_level_msid_has_precedence(self):
        item = api_listen(TIMESTAMP, 1)
        item['track_metadata']['additional_info'] = {'recording_msid': OTHER_MSID}
        self.assertEqual(Listen.from_api(item).recording_msid, MSID)

    def test_safe_read_retry_does_not_repeat_an_ambiguous_write(self):
        response = httpx.Response(
            200, json={}, request=httpx.Request('GET', 'https://test')
        )
        with (
            ListenBrainzClient('offline') as client,
            patch('lb_mapper.lb_client.time.sleep'),
        ):
            with patch.object(
                client._client,
                'request',
                side_effect=[httpx.ReadTimeout('offline'), response],
            ) as request:
                self.assertIs(client._request('GET', '/test'), response)
                self.assertEqual(request.call_count, 2)
            with patch.object(
                client._client, 'request', side_effect=httpx.ReadTimeout('offline')
            ) as request:
                with self.assertRaises(httpx.ReadTimeout):
                    client._request('POST', '/test')
                request.assert_called_once()

    def test_token_owner_mismatch_is_rejected_from_api_response(self):
        with ListenBrainzClient('token') as lb:
            response = httpx.Response(200, json={'valid': True, 'user_name': 'other'})
            with (
                patch.object(lb, '_request', return_value=response),
                self.assertRaisesRegex(ValueError, 'belong'),
            ):
                lb.validate_token('user')

    def test_occurrence_lookup_expands_and_detects_saturation(self):
        history = [api_listen(TIMESTAMP, i) for i in range(1000)]

        def respond(method, url, **kwargs):
            count = kwargs['params']['count']
            return httpx.Response(200, json={'payload': {'listens': history[:count]}})

        with (
            ListenBrainzClient('token') as lb,
            patch.object(lb, '_request', side_effect=respond),
        ):
            target = history[-1]['recording_msid']
            self.assertEqual(
                lb.get_listen('user', TIMESTAMP, target).recording_msid, target
            )
            with self.assertRaisesRegex(RuntimeError, 'saturated'):
                lb.get_listen('user', TIMESTAMP, OTHER_MBID)


if __name__ == '__main__':
    unittest.main()

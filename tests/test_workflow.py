"""Behavioral regressions for listen collection, review, and execution."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

import httpx

from lb_mapper import lb_search, mb_search
from lb_mapper.cli import execute, repair_jsonl, search_batch
from lb_mapper.history import iter_export
from lb_mapper.lb_client import Listen, ListenBrainzClient
from lb_mapper.review import group_listens, prepare_actions


MSID = '00000000-0000-0000-0000-000000000001'
OTHER_MSID = '00000000-0000-0000-0000-000000000002'
MBID = '10000000-0000-0000-0000-000000000001'
OTHER_MBID = '10000000-0000-0000-0000-000000000002'
TIMESTAMP = 1700000000


def api_listen(timestamp: int, index: int) -> dict:
    return {
        'listened_at': timestamp,
        'recording_msid': f'00000000-0000-0000-0000-{index:012d}',
        'track_metadata': {'artist_name': 'Artist', 'track_name': f'Track {index}'},
    }


def occurrence(timestamp: int = TIMESTAMP, msid: str = MSID) -> dict:
    return {
        'listened_at': timestamp,
        'recording_msid': msid,
        'artist': 'Artist',
        'track': 'Title',
        'release': 'Release',
    }


class CollectionTests(unittest.TestCase):
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


class SearchTests(unittest.TestCase):
    def test_outage_is_error_with_no_false_empty_result(self):
        with (
            patch.object(lb_search, 'search_recording'),
            patch.object(
                search_batch,
                'search_recording',
                side_effect=httpx.ConnectError('offline'),
            ),
        ):
            row = search_batch._search_one({'artist': 'A', 'track': 'B'}, 'labs')
        self.assertEqual(row['status'], 'error')
        self.assertIn('ConnectError', row['error'])
        self.assertNotIn('results', row)

    def test_successful_empty_search_is_distinct(self):
        with patch.object(search_batch, 'search_recording', return_value=[]):
            row = search_batch._search_one({'artist': 'A', 'track': 'B'}, 'labs')
        self.assertEqual(row['status'], 'ok')
        self.assertEqual(row['results'], [])

    def test_resume_skips_success_but_retries_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            queries = Path(directory) / 'queries.json'
            output = Path(directory) / 'results.jsonl'
            queries.write_text(
                json.dumps(
                    [
                        {'recording_msid': MSID, 'artist': 'A', 'track': 'B'},
                        {'recording_msid': OTHER_MSID, 'artist': 'C', 'track': 'D'},
                    ]
                )
            )
            output.write_text(
                '\n'.join(
                    json.dumps(row)
                    for row in [
                        {
                            'recording_msid': MSID,
                            'source': 'labs',
                            'query': 'A B',
                            'status': 'ok',
                            'results': [{'recording_mbid': MBID}],
                        },
                        {
                            'recording_msid': OTHER_MSID,
                            'source': 'labs',
                            'query': 'C D',
                            'status': 'error',
                            'error': 'offline',
                        },
                    ]
                )
                + '\n'
            )
            with (
                patch(
                    'sys.argv',
                    ['search', '--input', str(queries), '--output', str(output)],
                ),
                patch.object(search_batch, 'search_recording', return_value=[]) as f,
            ):
                search_batch.main()
            f.assert_called_once_with('C', 'D')
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(rows[-1]['recording_msid'], OTHER_MSID)
            self.assertEqual(rows[-1]['status'], 'ok')

    def test_malformed_api_result_propagates(self):
        client = Mock()
        client.post.return_value = httpx.Response(
            200,
            json={'error': 'wrong schema'},
            request=httpx.Request('POST', 'https://test'),
        )
        with (
            patch.object(lb_search, '_get_client', return_value=client),
            self.assertRaisesRegex(ValueError, 'malformed'),
        ):
            lb_search.search_recording('A', 'B')

    def test_cjk_punctuation_and_supplementary_ideographs(self):
        self.assertFalse(lb_search.contains_cjk('Title。'))
        self.assertTrue(lb_search.contains_cjk('𠀀'))
        self.assertTrue(lb_search.contains_cjk('アルフレッド'))

    def test_musicbrainz_lookup_url_and_metadata_includes(self):

        def respond(request):
            self.assertEqual(
                str(request.url).split('?')[0],
                f'https://musicbrainz.org/ws/2/recording/{MBID}',
            )
            self.assertEqual(
                request.url.params['inc'], 'artist-credits+releases+isrcs+work-rels'
            )
            return httpx.Response(200, json={'id': MBID})

        with (
            httpx.Client(
                base_url='https://musicbrainz.org/ws/2',
                transport=httpx.MockTransport(respond),
            ) as client,
            patch.object(mb_search, '_get_client', return_value=client),
            patch.object(mb_search.time, 'sleep'),
        ):
            self.assertEqual(mb_search.lookup_recording(MBID), {'id': MBID})

    def test_search_cache_does_not_suppress_recording_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queries.json'
            path.write_text(
                json.dumps(
                    [
                        {'recording_msid': MSID, 'query': MBID},
                        {'recording_msid': MSID, 'recording_mbid': MBID},
                    ]
                )
            )
            output = io.StringIO()
            with (
                patch(
                    'sys.argv',
                    ['search', '--source', 'musicbrainz', '--input', str(path)],
                ),
                patch.object(mb_search, 'search_recordings', return_value=[]) as search,
                patch.object(
                    mb_search, 'lookup_recording', return_value={'id': MBID}
                ) as lookup,
                redirect_stdout(output),
            ):
                search_batch.main()
            search.assert_called_once_with(MBID)
            lookup.assert_called_once_with(MBID)
            rows = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(rows[-1]['results'], [{'id': MBID}])
            self.assertEqual(rows[-1]['operation'], 'lookup')


class ReviewTests(unittest.TestCase):
    def test_repeated_msid_has_one_mapping_and_preserves_occurrences(self):
        snapshot = {
            'user': 'user',
            'unlinked': [occurrence(), occurrence(TIMESTAMP - 1)],
        }
        groups = group_listens(snapshot['unlinked'])
        self.assertEqual(groups[0]['listens'], snapshot['unlinked'])
        actions = prepare_actions(
            snapshot,
            [
                {
                    'recording_msid': MSID,
                    'verdict': 'substitute',
                    'recording_mbid': MBID,
                    'reason': 'Same catalog and movement, alternate performer',
                    'evidence': ['completed catalog query'],
                }
            ],
        )
        self.assertEqual(
            actions['mappings'], [{'recording_msid': MSID, 'recording_mbid': MBID}]
        )
        self.assertEqual(actions['deletions'], [])

    def test_delete_expands_every_occurrence_after_complete_search(self):
        snapshot = {
            'user': 'user',
            'unlinked': [occurrence(), occurrence(TIMESTAMP - 1)],
        }
        decision = {
            'recording_msid': MSID,
            'verdict': 'delete',
            'reason': 'No accepted candidate',
            'evidence': ['query'],
            'search_complete': False,
        }
        with self.assertRaisesRegex(ValueError, 'completed search'):
            prepare_actions(snapshot, [decision])
        decision['search_complete'] = True
        self.assertEqual(
            prepare_actions(snapshot, [decision])['deletions'],
            [
                {'listened_at': TIMESTAMP, 'recording_msid': MSID},
                {'listened_at': TIMESTAMP - 1, 'recording_msid': MSID},
            ],
        )

    def test_missing_duplicate_and_unknown_decisions_fail(self):
        snapshot = {
            'user': 'user',
            'unlinked': [occurrence(), occurrence(msid=OTHER_MSID)],
        }
        decision = {
            'recording_msid': MSID,
            'verdict': 'skip',
            'reason': 'Query outage',
            'evidence': ['error record'],
        }
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            prepare_actions(snapshot, [decision])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            prepare_actions(snapshot, [decision, decision])
        with self.assertRaisesRegex(ValueError, 'unknown'):
            prepare_actions({'user': 'user', 'unlinked': []}, [decision])


class ExecutionTests(unittest.TestCase):
    def test_invalid_late_item_prevents_client_creation(self):
        data = {
            'user': 'user',
            'mappings': [
                {'recording_msid': MSID, 'recording_mbid': MBID},
                {'recording_msid': OTHER_MSID, 'recording_mbid': 'invalid'},
            ],
        }
        with (
            patch('sys.argv', ['execute', '--apply']),
            patch('sys.stdin', io.StringIO(json.dumps(data))),
            patch.object(execute, 'ListenBrainzClient') as client,
            self.assertRaises(SystemExit) as exc,
        ):
            execute.main()
        self.assertEqual(exc.exception.code, 2)
        client.assert_not_called()

    def test_duplicate_actions_collapse_and_conflicts_fail(self):
        item = {'recording_msid': MSID, 'recording_mbid': MBID}
        self.assertEqual(
            execute.validate_actions({'user': 'user', 'mappings': [item, item]})[
                'mappings'
            ],
            [item],
        )
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            execute.validate_actions(
                {
                    'user': 'user',
                    'mappings': [
                        item,
                        {'recording_msid': MSID, 'recording_mbid': OTHER_MBID},
                    ],
                }
            )
        with self.assertRaisesRegex(ValueError, 'map and delete'):
            execute.validate_actions(
                {'user': 'user', 'mappings': [item], 'deletions': [occurrence()]}
            )

    def test_mapping_readback_and_existing_conflict(self):
        lb = Mock()
        item = {'recording_msid': MSID, 'recording_mbid': MBID}
        lb.get_manual_mapping.side_effect = [None, MBID]
        self.assertEqual(execute.apply_mapping(lb, item), 'mapped')
        lb.submit_mapping.assert_called_once_with(MSID, MBID)
        lb.reset_mock(side_effect=True)
        lb.get_manual_mapping.return_value = OTHER_MBID
        with self.assertRaisesRegex(ValueError, 'different manual mapping'):
            execute.apply_mapping(lb, item)
        lb.submit_mapping.assert_not_called()

    def test_deletion_rechecks_current_link_and_reports_scheduling(self):
        lb = Mock()
        lb.get_listen.return_value = Listen(TIMESTAMP, MSID, 'A', 'B', '', None)
        lb.get_manual_mapping.return_value = None
        self.assertEqual(execute.apply_deletion(lb, 'user', occurrence()), 'scheduled')
        lb.delete_listen.assert_called_once_with(TIMESTAMP, MSID)
        lb.reset_mock()
        lb.get_listen.return_value = Listen(
            TIMESTAMP, MSID, 'A', 'B', '', {'recording_mbid': MBID}
        )
        with self.assertRaisesRegex(ValueError, 'now linked'):
            execute.apply_deletion(lb, 'user', occurrence())
        lb.delete_listen.assert_not_called()

    def test_batch_failure_returns_nonzero_and_keeps_result_record(self):
        data = {
            'user': 'user',
            'mappings': [{'recording_msid': MSID, 'recording_mbid': MBID}],
        }
        with (
            patch('sys.argv', ['execute', '--apply']),
            patch('sys.stdin', io.StringIO(json.dumps(data))),
            patch.object(execute, 'require_env', side_effect=['user', 'offline']),
            patch.object(execute, 'ListenBrainzClient'),
            patch.object(
                execute, 'apply_mapping', side_effect=httpx.ConnectError('offline')
            ),
        ):
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as exc:
                execute.main()
        self.assertEqual(exc.exception.code, 1)
        row = json.loads(output.getvalue())
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['recording_msid'], MSID)


class ArtifactTests(unittest.TestCase):
    def test_resume_repairs_partial_tail_and_preserves_complete_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rows.jsonl'
            path.write_bytes(b'{"status":"ok"}\n{"status":"')
            repair_jsonl(path)
            self.assertEqual(path.read_bytes(), b'{"status":"ok"}\n')
            path.write_bytes(b'{"status":"ok"}')
            repair_jsonl(path)
            self.assertEqual(path.read_bytes(), b'{"status":"ok"}\n')
            path.write_bytes(b'invalid\n{"status":"ok"}\n')
            with self.assertRaisesRegex(ValueError, 'Malformed'):
                repair_jsonl(path)

    def test_export_orders_numeric_months_and_normalizes_times(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'history.zip'
            with ZipFile(path, 'w') as archive:
                archive.writestr('user.json', json.dumps({'username': 'user'}))
                archive.writestr('listens/2026/2.jsonl', json.dumps(api_listen(10, 1)))
                late = api_listen(30.0, 3)
                late['track_metadata']['additional_info'] = {'isrc': 'TEST'}
                archive.writestr(
                    'listens/2026/10.jsonl',
                    '\n'.join(
                        [
                            json.dumps(api_listen(20, 2)),
                            json.dumps(late),
                        ]
                    ),
                )
            actual = list(iter_export(path, 'user'))
            self.assertEqual([item.listened_at for item in actual], [30, 20, 10])
            self.assertIs(type(actual[0].listened_at), int)
            self.assertEqual(actual[0].additional_info, {'isrc': 'TEST'})
            with self.assertRaisesRegex(ValueError, 'belong'):
                list(iter_export(path, 'other-user'))


if __name__ == '__main__':
    unittest.main()

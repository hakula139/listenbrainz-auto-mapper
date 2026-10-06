"""Behavioral regressions for mapper execution contracts."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import httpx

from lb_mapper.cli import (
    execute,
    export_history,
    verify,
)
from lb_mapper.lb_client import Listen
from lb_mapper.review import validate_actions
from tests.fixtures import (
    MBID,
    MSID,
    OTHER_MBID,
    OTHER_MSID,
    TIMESTAMP,
    occurrence,
)


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
            validate_actions({'user': 'user', 'mappings': [item, item]})['mappings'],
            [item],
        )
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            validate_actions(
                {
                    'user': 'user',
                    'mappings': [
                        item,
                        {'recording_msid': MSID, 'recording_mbid': OTHER_MBID},
                    ],
                }
            )
        with self.assertRaisesRegex(ValueError, 'map and delete'):
            validate_actions(
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


class CLITests(unittest.TestCase):
    def test_overlapping_output_rejected_before_plan_is_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'actions.json'
            original = json.dumps({'user': 'user', 'mappings': []})
            path.write_text(original)

            with (
                patch(
                    'sys.argv', ['verify', '--input', str(path), '--output', str(path)]
                ),
                patch.object(verify, 'ListenBrainzClient') as client,
                self.assertRaises(SystemExit) as exc,
            ):
                verify.main()

            self.assertEqual(exc.exception.code, 2)
            self.assertEqual(path.read_text(), original)
            client.assert_not_called()

    def test_verification_auth_failure_preserves_previous_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'verification.jsonl'
            output.write_text('previous report\n')
            lb = MagicMock()
            lb.__enter__.return_value = lb
            lb.validate_token.side_effect = ValueError('Wrong owner')

            with (
                patch('sys.argv', ['verify', '--output', str(output)]),
                patch('sys.stdin', io.StringIO(json.dumps({'user': 'user'}))),
                patch.object(verify, 'load_dotenv'),
                patch.object(verify, 'require_env', side_effect=['user', 'token']),
                patch.object(verify, 'ListenBrainzClient', return_value=lb),
                self.assertRaisesRegex(ValueError, 'Wrong owner'),
            ):
                verify.main()

            self.assertEqual(output.read_text(), 'previous report\n')

    def test_verification_distinguishes_pending_mismatch_and_query_error(self):
        for action, state, expected, status in (
            ('deletions', occurrence(), 2, 'pending'),
            ('mappings', OTHER_MBID, 1, 'mismatch'),
            ('mappings', httpx.ConnectError('offline'), 1, 'error'),
        ):
            with self.subTest(status=status):
                item = (
                    occurrence()
                    if action == 'deletions'
                    else {'recording_msid': MSID, 'recording_mbid': MBID}
                )
                lb = MagicMock()
                lb.__enter__.return_value = lb
                lb.get_listen.return_value = state
                if isinstance(state, Exception):
                    lb.get_manual_mapping.side_effect = state
                else:
                    lb.get_manual_mapping.return_value = state
                output = io.StringIO()

                with (
                    patch('sys.argv', ['verify']),
                    patch(
                        'sys.stdin',
                        io.StringIO(json.dumps({'user': 'user', action: [item]})),
                    ),
                    patch.object(verify, 'load_dotenv'),
                    patch.object(verify, 'require_env', side_effect=['user', 'token']),
                    patch.object(verify, 'ListenBrainzClient', return_value=lb),
                    redirect_stdout(output),
                    self.assertRaises(SystemExit) as exc,
                ):
                    verify.main()

                self.assertEqual(exc.exception.code, expected)
                self.assertEqual(json.loads(output.getvalue())['status'], status)

    def test_export_state_cannot_collide_with_output_and_confirms_download(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'history.json'
            lb = MagicMock()
            lb.__enter__.return_value = lb
            lb.list_exports.return_value = []
            lb.request_export.return_value = {'export_id': 7, 'status': 'completed'}
            lb.get_export.return_value = {'export_id': 7, 'status': 'completed'}
            lb.download_export.side_effect = lambda _, path: path.write_bytes(
                b'archive'
            )

            with (
                patch('sys.argv', ['export', '--output', str(output)]),
                patch.object(export_history, 'load_dotenv'),
                patch.object(
                    export_history,
                    'require_env',
                    side_effect=['user', 'token', 'user', 'token'],
                ),
                patch.object(export_history, 'ListenBrainzClient', return_value=lb),
            ):
                export_history.main()
                self.assertEqual(output.read_bytes(), b'archive')
                lb.download_export.assert_called_once_with(7, output)
                export_history.main()
                lb.download_export.assert_called_once()

            state = json.loads(output.with_suffix('.json.json').read_text())
            self.assertTrue(state['downloaded'])
            self.assertEqual(state['export']['export_id'], 7)

    def test_existing_archive_without_state_is_preserved_before_api_work(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'history.zip'
            output.write_bytes(b'unknown archive')
            with (
                patch('sys.argv', ['export', '--output', str(output)]),
                patch.object(export_history, 'ListenBrainzClient') as client,
                self.assertRaises(SystemExit) as exc,
            ):
                export_history.main()
            self.assertEqual(exc.exception.code, 2)
            self.assertEqual(output.read_bytes(), b'unknown archive')
            client.assert_not_called()

    def test_legacy_export_state_resumes_same_job_and_migrates_download_record(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'history.zip'
            output.write_bytes(b'old archive')
            output.with_suffix('.json').write_text(
                json.dumps(
                    {
                        'user': 'user',
                        'export': {'export_id': 7, 'status': 'completed'},
                    }
                )
            )
            lb = MagicMock()
            lb.__enter__.return_value = lb
            lb.get_export.return_value = {'export_id': 7, 'status': 'completed'}
            lb.download_export.side_effect = lambda _, path: path.write_bytes(
                b'fresh archive'
            )
            with (
                patch('sys.argv', ['export', '--output', str(output)]),
                patch.object(export_history, 'load_dotenv'),
                patch.object(
                    export_history, 'require_env', side_effect=['user', 'token']
                ),
                patch.object(export_history, 'ListenBrainzClient', return_value=lb),
            ):
                export_history.main()
            lb.get_export.assert_called_once_with(7)
            lb.request_export.assert_not_called()
            self.assertEqual(output.read_bytes(), b'fresh archive')
            state = json.loads(output.with_suffix('.zip.json').read_text())
            self.assertTrue(state['downloaded'])
            self.assertEqual(state['export']['export_id'], 7)


if __name__ == '__main__':
    unittest.main()

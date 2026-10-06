"""Behavioral regressions for mapper artifact contracts."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from lb_mapper.cli import repair_jsonl
from lb_mapper.cli.prepare import main as prepare_main
from lb_mapper.cli.search_batch import main as search_main
from lb_mapper.history import iter_export
from tests.fixtures import MBID, MSID, api_listen, occurrence


class ArtifactTests(unittest.TestCase):
    def test_search_rejects_invalid_queries_before_requests_or_journal_writes(self):
        for query in ('', '   ', None, 123):
            with self.subTest(query=query), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'queries.json'
                output = root / 'musicbrainz.jsonl'
                source.write_text(
                    json.dumps(
                        [
                            {'recording_msid': MSID, 'query': 'recording:"Title"'},
                            {'recording_msid': MSID, 'query': query},
                        ]
                    )
                )
                output.write_text('existing journal')
                argv = [
                    'search_batch',
                    '--source',
                    'musicbrainz',
                    '--input',
                    str(source),
                    '--output',
                    str(output),
                ]

                with (
                    patch('sys.argv', argv),
                    patch('lb_mapper.cli.search_batch._search_one') as search,
                    redirect_stderr(StringIO()) as errors,
                    self.assertRaises(SystemExit) as failure,
                ):
                    search_main()

                self.assertEqual(failure.exception.code, 2)
                self.assertIn('non-empty string', errors.getvalue())
                self.assertEqual(output.read_text(), 'existing journal')
                search.assert_not_called()

    def test_prepare_requires_canonical_journal_before_writing_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / 'snapshot.json'
            decisions = root / 'decisions.json'
            journal = root / 'musicbrainz.jsonl'
            output = root / 'actions.json'
            snapshot.write_text(
                json.dumps({'user': 'user', 'unlinked': [occurrence()]})
            )
            decisions.write_text(
                json.dumps(
                    [
                        {
                            'recording_msid': MSID,
                            'verdict': 'link',
                            'recording_mbid': MBID,
                            'reason': 'Reviewed target',
                            'evidence': ['Recording lookup'],
                        }
                    ]
                )
            )
            output.write_text('existing plan')
            argv = ['prepare', str(snapshot), str(decisions), '--output', str(output)]

            with (
                patch('sys.argv', argv),
                redirect_stderr(StringIO()) as errors,
                self.assertRaises(SystemExit) as failure,
            ):
                prepare_main()

            self.assertEqual(failure.exception.code, 2)
            self.assertIn('Mappings require --recordings', errors.getvalue())
            self.assertEqual(output.read_text(), 'existing plan')

            journal.write_text(
                json.dumps(
                    {
                        'source': 'musicbrainz',
                        'operation': 'lookup',
                        'recording_msid': MSID,
                        'status': 'ok',
                        'results': [{'id': MBID}],
                    }
                )
                + '\n'
            )
            with patch('sys.argv', [*argv, '--recordings', str(journal)]):
                prepare_main()

            self.assertEqual(
                json.loads(output.read_text())['mappings'],
                [{'recording_msid': MSID, 'recording_mbid': MBID}],
            )

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

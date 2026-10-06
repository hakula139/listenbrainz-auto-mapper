"""Behavioral regressions for mapper artifact contracts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from lb_mapper.cli import repair_jsonl
from lb_mapper.history import iter_export
from tests.fixtures import api_listen


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

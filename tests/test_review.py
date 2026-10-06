"""Behavioral regressions for mapper review contracts."""

from __future__ import annotations

import unittest

from lb_mapper.review import group_listens, prepare_actions
from tests.fixtures import (
    MBID,
    MSID,
    OTHER_MSID,
    TIMESTAMP,
    occurrence,
)


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

    def test_preparation_rejects_invalid_occurrence_time(self):
        snapshot = {'user': 'user', 'unlinked': [occurrence(timestamp='invalid')]}
        decision = {
            'recording_msid': MSID,
            'verdict': 'delete',
            'reason': 'No match',
            'evidence': ['Completed recovery'],
            'search_complete': True,
        }
        with self.assertRaisesRegex(ValueError, 'listen time'):
            prepare_actions(snapshot, [decision])


if __name__ == '__main__':
    unittest.main()

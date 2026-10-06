"""Behavioral regressions for mapper execution contracts."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

from lb_mapper.cli import execute, export_history, verify
from lb_mapper.lb_client import Listen
from lb_mapper.review import DeletionAction, MappingAction, validate_actions
from tests.fixtures import MBID, MSID, OTHER_MBID, OTHER_MSID, TIMESTAMP, occurrence


def test_invalid_late_item_prevents_client_creation() -> None:
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
        pytest.raises(SystemExit) as exc,
    ):
        execute.main()

    assert exc.value.code == 2

    client.assert_not_called()


def test_duplicate_actions_collapse_and_conflicts_fail() -> None:
    item = {'recording_msid': MSID, 'recording_mbid': MBID}

    assert validate_actions({'user': 'user', 'mappings': [item, item]})['mappings'] == [
        item
    ]

    with pytest.raises(ValueError, match='Conflicting'):
        validate_actions(
            {
                'user': 'user',
                'mappings': [
                    item,
                    {'recording_msid': MSID, 'recording_mbid': OTHER_MBID},
                ],
            }
        )

    with pytest.raises(ValueError, match='map and delete'):
        validate_actions(
            {'user': 'user', 'mappings': [item], 'deletions': [occurrence()]}
        )

    with pytest.raises(ValueError, match='Conflicting'):
        validate_actions(
            {
                'user': 'user',
                'mappings': [item, {**item, 'previous_recording_mbid': OTHER_MBID}],
            }
        )


def test_reviewed_replacement_rechecks_current_mapping() -> None:
    lb = Mock()
    item: MappingAction = {
        'recording_msid': MSID,
        'recording_mbid': MBID,
        'previous_recording_mbid': OTHER_MBID,
    }

    for current in (OTHER_MBID, None):
        lb.reset_mock(side_effect=True)
        lb.get_manual_mapping.side_effect = [current, MBID]
        assert execute.apply_mapping(lb, item) == 'mapped'
        lb.submit_mapping.assert_called_once_with(MSID, MBID)

    lb.reset_mock(side_effect=True)
    lb.get_manual_mapping.return_value = OTHER_MSID

    with pytest.raises(ValueError, match='different manual mapping'):
        execute.apply_mapping(lb, item)

    lb.submit_mapping.assert_not_called()


def test_mapping_readback_and_existing_conflict() -> None:
    lb = Mock()
    item: MappingAction = {'recording_msid': MSID, 'recording_mbid': MBID}
    lb.get_manual_mapping.side_effect = [None, MBID]

    assert execute.apply_mapping(lb, item) == 'mapped'

    lb.submit_mapping.assert_called_once_with(MSID, MBID)
    lb.reset_mock(side_effect=True)
    lb.get_manual_mapping.return_value = OTHER_MBID

    with pytest.raises(ValueError, match='different manual mapping'):
        execute.apply_mapping(lb, item)

    lb.submit_mapping.assert_not_called()


def test_deletion_rechecks_current_link_and_reports_scheduling() -> None:
    lb = Mock()
    lb.get_listen.return_value = Listen(TIMESTAMP, MSID, 'A', 'B', '', None)
    lb.get_manual_mapping.return_value = None

    item: DeletionAction = {'listened_at': TIMESTAMP, 'recording_msid': MSID}

    assert execute.apply_deletion(lb, 'user', item) == 'scheduled'

    lb.delete_listen.assert_called_once_with(TIMESTAMP, MSID)
    lb.reset_mock()
    lb.get_listen.return_value = Listen(
        TIMESTAMP, MSID, 'A', 'B', '', {'recording_mbid': MBID}
    )

    with pytest.raises(ValueError, match='now linked'):
        execute.apply_deletion(lb, 'user', item)

    lb.delete_listen.assert_not_called()


def test_batch_failure_returns_nonzero_and_keeps_result_record() -> None:
    data = {
        'user': 'user',
        'mappings': [
            {'recording_msid': MSID, 'recording_mbid': MBID},
            {'recording_msid': OTHER_MSID, 'recording_mbid': OTHER_MBID},
        ],
        'deletions': [occurrence(msid=OTHER_MBID)],
    }

    with (
        patch('sys.argv', ['execute', '--apply']),
        patch('sys.stdin', io.StringIO(json.dumps(data))),
        patch.object(execute, 'require_env', side_effect=['user', 'offline']),
        patch.object(execute, 'ListenBrainzClient'),
        patch.object(
            execute, 'apply_mapping', side_effect=httpx.ConnectError('offline')
        ) as mapping,
        patch.object(execute, 'apply_deletion') as deletion,
    ):
        output = io.StringIO()
        with redirect_stdout(output), pytest.raises(SystemExit) as exc:
            execute.main()

    assert exc.value.code == 1

    row = json.loads(output.getvalue())

    assert row['status'] == 'error'
    assert row['recording_msid'] == MSID

    mapping.assert_called_once()
    deletion.assert_not_called()


def test_hardlinked_output_is_rejected_before_account_requests(tmp_path: Path) -> None:
    source = tmp_path / 'actions.json'
    output = tmp_path / 'verification.jsonl'
    source.write_text(json.dumps({'user': 'user'}))
    output.hardlink_to(source)

    with (
        patch('sys.argv', ['verify', '--input', str(source), '--output', str(output)]),
        patch.object(verify, 'ListenBrainzClient') as client,
        pytest.raises(SystemExit) as failure,
    ):
        verify.main()

    assert failure.value.code == 2
    assert json.loads(source.read_text()) == {'user': 'user'}

    client.assert_not_called()


def test_interrupted_verification_appends_observations_without_erasing_history(
    tmp_path: Path,
) -> None:
    output = tmp_path / 'verification.jsonl'
    previous = {'status': 'verified', 'recording_msid': MSID}
    output.write_text(json.dumps(previous) + '\n')
    lb = MagicMock()
    lb.__enter__.return_value = lb
    lb.get_manual_mapping.side_effect = [MBID, KeyboardInterrupt()]
    plan = {
        'user': 'user',
        'mappings': [
            {'recording_msid': MSID, 'recording_mbid': MBID},
            {'recording_msid': OTHER_MSID, 'recording_mbid': OTHER_MBID},
        ],
    }

    with (
        patch('sys.argv', ['verify', '--output', str(output)]),
        patch('sys.stdin', io.StringIO(json.dumps(plan))),
        patch.object(verify, 'load_dotenv'),
        patch.object(verify, 'require_env', side_effect=['user', 'token']),
        patch.object(verify, 'ListenBrainzClient', return_value=lb),
        pytest.raises(KeyboardInterrupt),
    ):
        verify.main()

    rows = [json.loads(line) for line in output.read_text().splitlines()]

    assert rows[0] == previous
    assert rows[1]['recording_msid'] == MSID
    assert rows[1]['status'] == 'verified'
    assert 'observed_at' in rows[1]
    assert len(rows) == 2


def test_overlapping_output_rejected_before_plan_is_overwritten(tmp_path: Path) -> None:
    path = tmp_path / 'actions.json'
    original = json.dumps({'user': 'user', 'mappings': []})
    path.write_text(original)

    with (
        patch('sys.argv', ['verify', '--input', str(path), '--output', str(path)]),
        patch.object(verify, 'ListenBrainzClient') as client,
        pytest.raises(SystemExit) as exc,
    ):
        verify.main()

    assert exc.value.code == 2
    assert path.read_text() == original

    client.assert_not_called()


def test_verification_auth_failure_preserves_previous_report(tmp_path: Path) -> None:
    output = tmp_path / 'verification.jsonl'
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
        pytest.raises(ValueError, match='Wrong owner'),
    ):
        verify.main()

    assert output.read_text() == 'previous report\n'


@pytest.mark.parametrize(
    'action, state, expected, status',
    (
        ('deletions', Listen(TIMESTAMP, MSID, 'A', 'B', '', None), 2, 'pending'),
        ('mappings', OTHER_MBID, 1, 'mismatch'),
        ('mappings', httpx.ConnectError('offline'), 1, 'error'),
    ),
)
def test_verification_distinguishes_pending_mismatch_and_query_error(
    action: str,
    state: Listen | str | httpx.HTTPError,
    expected: int,
    status: str,
) -> None:
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
        patch('sys.stdin', io.StringIO(json.dumps({'user': 'user', action: [item]}))),
        patch.object(verify, 'load_dotenv'),
        patch.object(verify, 'require_env', side_effect=['user', 'token']),
        patch.object(verify, 'ListenBrainzClient', return_value=lb),
        redirect_stdout(output),
        pytest.raises(SystemExit) as exc,
    ):
        verify.main()

    assert exc.value.code == expected
    assert json.loads(output.getvalue())['status'] == status


def test_export_state_cannot_collide_with_output_and_confirms_download(
    tmp_path: Path,
) -> None:
    output = tmp_path / 'history.json'
    lb = MagicMock()
    lb.__enter__.return_value = lb
    lb.list_exports.return_value = []
    lb.request_export.return_value = {'export_id': 7, 'status': 'completed'}
    lb.get_export.return_value = {'export_id': 7, 'status': 'completed'}
    lb.download_export.side_effect = lambda _, path: path.write_bytes(b'archive')

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
        assert output.read_bytes() == b'archive'
        lb.download_export.assert_called_once_with(7, output)
        export_history.main()
        lb.download_export.assert_called_once()

    state = json.loads(output.with_suffix('.json.json').read_text())

    assert state['downloaded']
    assert state['export']['export_id'] == 7


def test_existing_archive_without_state_is_preserved_before_api_work(
    tmp_path: Path,
) -> None:
    output = tmp_path / 'history.zip'
    output.write_bytes(b'unknown archive')

    with (
        patch('sys.argv', ['export', '--output', str(output)]),
        patch.object(export_history, 'ListenBrainzClient') as client,
        pytest.raises(SystemExit) as exc,
    ):
        export_history.main()

    assert exc.value.code == 2
    assert output.read_bytes() == b'unknown archive'

    client.assert_not_called()


def test_legacy_export_state_resumes_same_job_and_migrates_download_record(
    tmp_path: Path,
) -> None:
    output = tmp_path / 'history.zip'
    output.write_bytes(b'old archive')
    output.with_suffix('.json').write_text(
        json.dumps({'user': 'user', 'export': {'export_id': 7, 'status': 'completed'}})
    )
    lb = MagicMock()
    lb.__enter__.return_value = lb
    lb.get_export.return_value = {'export_id': 7, 'status': 'completed'}
    lb.download_export.side_effect = lambda _, path: path.write_bytes(b'fresh archive')

    with (
        patch('sys.argv', ['export', '--output', str(output)]),
        patch.object(export_history, 'load_dotenv'),
        patch.object(export_history, 'require_env', side_effect=['user', 'token']),
        patch.object(export_history, 'ListenBrainzClient', return_value=lb),
    ):
        export_history.main()

    lb.get_export.assert_called_once_with(7)
    lb.request_export.assert_not_called()

    assert output.read_bytes() == b'fresh archive'

    state = json.loads(output.with_suffix('.zip.json').read_text())

    assert state['downloaded']
    assert state['export']['export_id'] == 7

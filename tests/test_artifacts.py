"""Behavioral regressions for mapper artifact contracts."""

from __future__ import annotations

import json
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from lb_mapper.artifacts import atomic_path, repair_jsonl, write_json
from lb_mapper.cli.prepare import main as prepare_main
from lb_mapper.cli.search_batch import main as search_main
from lb_mapper.history import iter_export
from tests.fixtures import MBID, MSID, api_listen, occurrence, recording


def test_atomic_write_preserves_a_colliding_input_and_failed_destination(
    tmp_path: Path,
) -> None:
    output = tmp_path / 'actions.json'
    source = tmp_path / 'actions.json.tmp'
    output.write_text('previous plan')
    source.write_text('input snapshot')
    write_json({'user': 'user'}, output)

    assert source.read_text() == 'input snapshot'
    assert json.loads(output.read_text()) == {'user': 'user'}

    with (
        pytest.raises(RuntimeError, match='interrupted'),
        atomic_path(output) as staged,
    ):
        staged.write_text('partial new plan')
        raise RuntimeError('interrupted')

    assert json.loads(output.read_text()) == {'user': 'user'}
    assert source.read_text() == 'input snapshot'
    assert set(tmp_path.iterdir()) == {source, output}


@pytest.mark.parametrize('offset', ([], None, True, -1, '100'))
def test_invalid_offsets_fail_before_requests_and_journal_repair(
    tmp_path: Path, offset: object
) -> None:
    source = tmp_path / 'queries.json'
    output = tmp_path / 'musicbrainz.jsonl'
    source.write_text(
        json.dumps(
            [{'query': 'Title', 'offset': 0}, {'query': 'Title', 'offset': offset}]
        )
    )
    output.write_text('existing journal')

    with (
        patch(
            'sys.argv',
            [
                'search',
                '--source',
                'musicbrainz',
                '--input',
                str(source),
                '--output',
                str(output),
            ],
        ),
        patch('lb_mapper.cli.search_batch._search_one') as search,
        redirect_stderr(StringIO()),
        pytest.raises(SystemExit) as failure,
    ):
        search_main()

    assert failure.value.code == 2
    assert output.read_text() == 'existing journal'

    search.assert_not_called()


@pytest.mark.parametrize('query', ('', '   ', None, 123))
def test_search_rejects_invalid_queries_before_requests_or_journal_writes(
    tmp_path: Path, query: object
) -> None:
    source = tmp_path / 'queries.json'
    output = tmp_path / 'musicbrainz.jsonl'
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
        pytest.raises(SystemExit) as failure,
    ):
        search_main()

    assert failure.value.code == 2
    assert 'non-empty string' in errors.getvalue()
    assert output.read_text() == 'existing journal'

    search.assert_not_called()


def test_prepare_requires_canonical_journal_before_writing_plan(tmp_path: Path) -> None:
    snapshot = tmp_path / 'snapshot.json'
    decisions = tmp_path / 'decisions.json'
    journal = tmp_path / 'musicbrainz.jsonl'
    output = tmp_path / 'actions.json'
    snapshot.write_text(json.dumps({'user': 'user', 'unlinked': [occurrence()]}))
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
        pytest.raises(SystemExit) as failure,
    ):
        prepare_main()

    assert failure.value.code == 2
    assert 'Mappings require --recordings' in errors.getvalue()
    assert output.read_text() == 'existing plan'

    journal.write_text(
        json.dumps(
            {
                'source': 'musicbrainz',
                'operation': 'lookup',
                'recording_msid': MSID,
                'status': 'ok',
                'results': [recording(MBID)],
            }
        )
        + '\n'
    )

    with patch('sys.argv', [*argv, '--recordings', str(journal)]):
        prepare_main()

    assert json.loads(output.read_text())['mappings'] == [
        {'recording_msid': MSID, 'recording_mbid': MBID}
    ]


def test_resume_repairs_partial_tail_and_preserves_complete_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'rows.jsonl'
    path.write_bytes(b'{"status":"ok"}\n{"status":"')
    repair_jsonl(path)

    assert path.read_bytes() == b'{"status":"ok"}\n'

    path.write_bytes(b'{"status":"ok"}')
    repair_jsonl(path)

    assert path.read_bytes() == b'{"status":"ok"}\n'

    path.write_bytes(b'invalid\n{"status":"ok"}\n')

    with pytest.raises(ValueError, match='Malformed'):
        repair_jsonl(path)


def test_export_orders_numeric_months_and_normalizes_times(tmp_path: Path) -> None:
    path = tmp_path / 'history.zip'

    with ZipFile(path, 'w') as archive:
        archive.writestr('user.json', json.dumps({'username': 'user'}))
        archive.writestr('listens/2026/2.jsonl', json.dumps(api_listen(10, 1)))
        late = api_listen(30.0, 3)
        late['track_metadata']['additional_info'] = {'isrc': 'TEST'}
        archive.writestr(
            'listens/2026/10.jsonl',
            '\n'.join([json.dumps(api_listen(20, 2)), json.dumps(late)]),
        )

    actual = list(iter_export(path, 'user'))

    assert [item.listened_at for item in actual] == [30, 20, 10]
    assert type(actual[0].listened_at) is int
    assert actual[0].additional_info == {'isrc': 'TEST'}

    with pytest.raises(ValueError, match='belong'):
        list(iter_export(path, 'other-user'))

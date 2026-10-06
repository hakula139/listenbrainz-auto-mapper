"""Behavioral regressions for mapper search contracts."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import Mock, call, patch

import httpx
import pytest

from lb_mapper import lb_search, mb_search
from lb_mapper.cli import lookup_batch, search_batch
from lb_mapper.cli.search_journal import SearchJournal
from tests.fixtures import MBID, MSID, OTHER_MBID, OTHER_MSID, recording


@pytest.mark.parametrize(
    'changes', ({'track': None}, {'artist': []}, {'recording_msid': 'invalid'})
)
def test_invalid_late_bulk_input_preserves_journal_before_lookup(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    path = tmp_path / 'queries.json'
    output = tmp_path / 'results.jsonl'
    valid = {'recording_msid': MSID, 'artist': 'A', 'track': 'B'}
    path.write_text(json.dumps([valid, {**valid, **changes}]))
    output.write_text('existing journal')

    with (
        patch('sys.argv', ['lookup', '--input', str(path), '--output', str(output)]),
        patch.object(lookup_batch, 'lookup_recordings') as lookup,
        redirect_stderr(io.StringIO()),
        pytest.raises(SystemExit) as failure,
    ):
        lookup_batch.main()

    assert failure.value.code == 2
    assert output.read_text() == 'existing journal'

    lookup.assert_not_called()


def test_bulk_lookup_routes_unordered_hits_and_preserves_collision_miss() -> None:
    client = Mock()
    hit = {
        'index': 1,
        'artist_credit_arg': 'A',
        'recording_arg': 'BC',
        'recording_mbid': MBID,
        'artist_credit_name': 'AB',
        'recording_name': 'C',
    }
    client.post.return_value = httpx.Response(
        200, json=[hit], request=httpx.Request('POST', 'https://test')
    )

    with (
        patch.object(lb_search, '_get_client', return_value=client),
        patch('lb_mapper.lb_search.time.sleep'),
    ):
        results = lb_search.lookup_recordings([('AB', 'C'), ('A', 'BC')])

    assert results == [[], [hit]]
    assert results[1][0]['recording_name'] == 'C'


@pytest.mark.parametrize('changes', ({'index': -1}, {'recording_arg': 'Foreign'}))
def test_bulk_lookup_rejects_foreign_index_and_arguments(
    changes: dict[str, object],
) -> None:
    hit = {
        'index': 0,
        'artist_credit_arg': 'A',
        'recording_arg': 'B',
        'recording_mbid': MBID,
        **changes,
    }
    client = Mock()
    client.post.return_value = httpx.Response(
        200, json=[hit], request=httpx.Request('POST', 'https://test')
    )

    with (
        patch.object(lb_search, '_get_client', return_value=client),
        patch('lb_mapper.lb_search.time.sleep'),
        pytest.raises(ValueError),
    ):
        lb_search.lookup_recordings([('A', 'B')])


def test_bulk_lookup_deduplicates_raw_pairs_and_reuses_resumed_candidates(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'queries.json'
    output = tmp_path / 'results.jsonl'
    path.write_text(
        json.dumps(
            [
                {'recording_msid': MSID, 'artist': 'A', 'track': 'B'},
                {'recording_msid': OTHER_MSID, 'artist': 'A', 'track': 'B'},
            ]
        )
    )

    with (
        patch('sys.argv', ['lookup', '--input', str(path), '--output', str(output)]),
        patch.object(
            lookup_batch, 'lookup_recordings', return_value=[[{'recording_mbid': MBID}]]
        ) as lookup,
    ):
        lookup_batch.main()
        lookup.assert_called_once_with([('A', 'B')])
        lookup.reset_mock()
        lookup_batch.main()
        lookup.assert_not_called()

    rows = [json.loads(line) for line in output.read_text().splitlines()]

    assert [row['recording_msid'] for row in rows] == [MSID, OTHER_MSID]
    assert rows[1]['results'][0]['recording_mbid'] == MBID


def test_outage_is_error_with_no_false_empty_result() -> None:
    with (
        patch.object(lb_search, 'search_recording'),
        patch.object(
            search_batch, 'search_recording', side_effect=httpx.ConnectError('offline')
        ),
    ):
        row = search_batch._search_one({'artist': 'A', 'track': 'B'}, 'labs')

    assert row['status'] == 'error'
    assert 'ConnectError' in row['error']
    assert 'results' not in row


def test_successful_empty_search_is_distinct() -> None:
    with patch.object(search_batch, 'search_recording', return_value=[]):
        row = search_batch._search_one({'artist': 'A', 'track': 'B'}, 'labs')

    assert row['status'] == 'ok'
    assert row['results'] == []


def test_resume_skips_success_but_retries_failure(tmp_path: Path) -> None:
    queries = tmp_path / 'queries.json'
    output = tmp_path / 'results.jsonl'
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
        patch('sys.argv', ['search', '--input', str(queries), '--output', str(output)]),
        patch.object(search_batch, 'search_recording', return_value=[]) as f,
    ):
        search_batch.main()

    f.assert_called_once_with('C', 'D')
    rows = [json.loads(line) for line in output.read_text().splitlines()]

    assert rows[-1]['recording_msid'] == OTHER_MSID
    assert rows[-1]['status'] == 'ok'


def test_malformed_api_result_propagates() -> None:
    client = Mock()
    client.post.return_value = httpx.Response(
        200,
        json={'error': 'wrong schema'},
        request=httpx.Request('POST', 'https://test'),
    )

    with (
        patch.object(lb_search, '_get_client', return_value=client),
        pytest.raises(ValueError, match='malformed'),
    ):
        lb_search.search_recording('A', 'B')


def test_musicbrainz_lookup_url_and_metadata_includes() -> None:

    def respond(request: httpx.Request) -> httpx.Response:
        assert (
            str(request.url).split('?')[0]
            == f'https://musicbrainz.org/ws/2/recording/{MBID}'
        )
        assert request.url.params['inc'] == 'artist-credits+releases+isrcs+work-rels'
        return httpx.Response(200, json=recording(MBID))

    with (
        httpx.Client(
            base_url='https://musicbrainz.org/ws/2',
            transport=httpx.MockTransport(respond),
        ) as client,
        patch.object(mb_search, '_get_client', return_value=client),
        patch('lb_mapper.mb_search.time.sleep'),
    ):
        assert mb_search.lookup_recording(MBID) == recording(MBID)


def test_search_cache_does_not_suppress_recording_lookup(tmp_path: Path) -> None:
    path = tmp_path / 'queries.json'
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
        patch('sys.argv', ['search', '--source', 'musicbrainz', '--input', str(path)]),
        patch.object(
            mb_search,
            'search_recordings',
            return_value={'recordings': [], 'count': 0, 'offset': 0},
        ) as search,
        patch.object(
            mb_search, 'lookup_recording', return_value={'id': MBID}
        ) as lookup,
        redirect_stdout(output),
    ):
        search_batch.main()

    search.assert_called_once_with(MBID, 0)
    lookup.assert_called_once_with(MBID)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]

    assert rows[-1]['results'] == [{'id': MBID}]
    assert rows[-1]['operation'] == 'lookup'


@pytest.mark.parametrize('legacy_operation', (True, False))
def test_incomplete_historical_lookups_refresh_once_then_resume(
    tmp_path: Path, legacy_operation: bool
) -> None:
    path = tmp_path / 'queries.json'
    output = tmp_path / 'results.jsonl'
    query = {'recording_msid': MSID, 'recording_mbid': MBID}
    path.write_text(json.dumps([query]))
    old = {
        **query,
        'source': 'musicbrainz',
        'query': MBID,
        'status': 'ok',
        'results': [recording()] if legacy_operation else [{'id': MBID}],
    }
    if not legacy_operation:
        old['operation'] = 'lookup'
    output.write_text(json.dumps(old) + '\n')

    with (
        patch(
            'sys.argv',
            [
                'search',
                '--source',
                'musicbrainz',
                '--input',
                str(path),
                '--output',
                str(output),
            ],
        ),
        patch.object(mb_search, 'lookup_recording', return_value=recording()) as lookup,
    ):
        search_batch.main()
        lookup.assert_called_once_with(MBID)
        lookup.reset_mock()
        search_batch.main()
        lookup.assert_not_called()

    rows = [json.loads(line) for line in output.read_text().splitlines()]

    assert rows[0] == old
    assert rows[1]['results'] == [recording()]
    assert rows[1]['operation'] == 'lookup'


def test_merged_recording_lookup_returns_surviving_identifier() -> None:
    events = []

    def respond(request: httpx.Request) -> httpx.Response:
        events.append('request')
        if request.url.path.endswith(MBID):
            return httpx.Response(
                301,
                headers={
                    'Location': f'https://musicbrainz.org/ws/2/recording/{OTHER_MBID}?fmt=json'
                },
            )
        assert request.url.params['inc'] == 'artist-credits+releases+isrcs+work-rels'
        return httpx.Response(200, json=recording(OTHER_MBID))

    with (
        httpx.Client(
            base_url='https://musicbrainz.org/ws/2',
            follow_redirects=True,
            transport=httpx.MockTransport(respond),
        ) as client,
        patch.object(mb_search, '_get_client', return_value=client),
        patch(
            'lb_mapper.mb_search.time.sleep',
            side_effect=lambda _: events.append('pace'),
        ),
    ):
        assert mb_search.lookup_recording(MBID)['id'] == OTHER_MBID

    assert events == ['pace', 'request', 'pace', 'request']


@pytest.mark.parametrize('persistent', (False, True))
def test_transient_musicbrainz_read_retries_are_paced_and_bounded(
    persistent: bool,
) -> None:
    events = []
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        events.append('request')
        if persistent or calls < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={'recordings': [], 'count': 0, 'offset': 0})

    with (
        httpx.Client(
            base_url='https://musicbrainz.org/ws/2',
            transport=httpx.MockTransport(respond),
        ) as client,
        patch.object(mb_search, '_get_client', return_value=client),
        patch(
            'lb_mapper.mb_search.time.sleep',
            side_effect=lambda _: events.append('pace'),
        ),
    ):
        if persistent:
            with pytest.raises(httpx.HTTPStatusError):
                mb_search.search_recordings('Title')
        else:
            assert mb_search.search_recordings('Title')['count'] == 0

    assert calls == 3
    assert events == [
        'pace',
        'request',
        'pace',
        'pace',
        'request',
        'pace',
        'pace',
        'request',
    ]


def test_legacy_search_without_pagination_cannot_satisfy_current_query(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'results.jsonl'
    row = {
        'recording_msid': MSID,
        'source': 'musicbrainz',
        'operation': 'search',
        'query': 'Title',
        'status': 'ok',
        'results': [{'id': MBID}],
    }
    original = json.dumps(row) + '\n'
    path.write_text(original)
    journal = SearchJournal(path, 'musicbrainz')

    assert not journal.is_complete(row)
    assert journal.cached(row) is None
    assert path.read_text() == original

    with path.open('a') as stream:
        journal.record({**row, 'result_count': 1, 'result_offset': 0}, stream)

    resumed = SearchJournal(path, 'musicbrainz')

    assert resumed.is_complete(row)
    cached = resumed.cached(row)

    assert cached is not None
    assert cached['result_count'] == 1


def test_search_pages_keep_total_count_and_distinct_resume_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'queries.json'
    output = tmp_path / 'results.jsonl'
    path.write_text(
        json.dumps(
            [
                {'recording_msid': MSID, 'query': 'Title', 'offset': 0},
                {'recording_msid': MSID, 'query': 'Title', 'offset': 100},
                {'recording_msid': OTHER_MSID, 'query': 'Title', 'offset': 100},
            ]
        )
    )

    def page(query: str, offset: int) -> dict[str, Any]:
        return {
            'recordings': [{'id': MBID if offset == 0 else OTHER_MBID}],
            'count': 101,
            'offset': offset,
        }

    with (
        patch(
            'sys.argv',
            [
                'search',
                '--source',
                'musicbrainz',
                '--input',
                str(path),
                '--output',
                str(output),
            ],
        ),
        patch.object(mb_search, 'search_recordings', side_effect=page) as search,
    ):
        search_batch.main()
        assert search.call_args_list == [call('Title', 0), call('Title', 100)]
        search.reset_mock()
        search_batch.main()
        search.assert_not_called()

    rows = [json.loads(line) for line in output.read_text().splitlines()]

    assert rows[0]['next_offset'] == 1
    assert 'next_offset' not in rows[1]
    assert rows[2]['result_count'] == 101
    assert rows[2]['results'] == [{'id': OTHER_MBID}]

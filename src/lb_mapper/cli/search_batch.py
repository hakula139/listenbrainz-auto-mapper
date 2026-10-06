"""Search unique MSIDs or explicit queries, streaming resumable JSONL results."""

from __future__ import annotations

import argparse
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import httpx

from lb_mapper import mb_search
from lb_mapper.cli import read_json, validate_paths
from lb_mapper.cli.search_journal import SearchJournal
from lb_mapper.lb_search import search_recording
from lb_mapper.review import group_listens, uuid_string


def _query(item: dict[str, Any], source: str) -> str:
    if source == 'labs':
        return f'{item["artist"]} {item["track"]}'.strip()
    if item.get('recording_mbid'):
        return uuid_string(item['recording_mbid'])
    if 'query' in item:
        query = item['query']
        if not isinstance(query, str) or not query.strip():
            raise ValueError('MusicBrainz query must be a non-empty string')

        return query

    return mb_search.recording_query(item['artist'], item['track'])


def _entry(item: dict[str, Any], source: str) -> dict[str, Any]:
    query = _query(item, source)
    operation = (
        'lookup' if source == 'musicbrainz' and item.get('recording_mbid') else 'search'
    )
    return {**item, 'source': source, 'operation': operation, 'query': query}


def _search_one(item: dict[str, Any], source: str) -> dict[str, Any]:
    entry = _entry(item, source)
    query = entry['query']

    try:
        if source == 'labs':
            results = search_recording(item['artist'], item['track'])
        elif item.get('recording_mbid'):
            results = [mb_search.lookup_recording(query)]
        else:
            page = mb_search.search_recordings(query, item.get('offset', 0))
            results = page['recordings']
            entry.update(result_count=page['count'], result_offset=page['offset'])

            next_offset = page['offset'] + len(results)
            if next_offset < page['count']:
                entry['next_offset'] = next_offset

        entry.update(status='ok', results=results)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        entry.update(status='error', error=f'{type(exc).__name__}: {exc}')

    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--source', choices=('labs', 'musicbrainz'), default='labs')
    args = parser.parse_args()
    validate_paths(parser, args.input, args.output)

    data = read_json(args.input)
    items = group_listens(data['unlinked']) if isinstance(data, dict) else data
    if not isinstance(items, list):
        parser.error('input must be a listen snapshot or an array of queries')

    try:
        entries = [_entry(item, args.source) for item in items]
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(f'Invalid search input: {exc}')

    journal = SearchJournal(args.output, args.source)

    failed = False
    context = args.output.open('a') if args.output else nullcontext(sys.stdout)
    with context as stream:
        for i, entry in enumerate(entries, 1):
            if journal.is_complete(entry):
                continue

            outcome = journal.cached(entry)
            if outcome is not None:
                entry.update(outcome)
            else:
                entry = _search_one(entry, args.source)

            journal.record(entry, stream)
            failed |= entry['status'] == 'error'
            print(
                f'[{i}/{len(items)}] {entry.get("artist", "")} / '
                f'{entry.get("track", entry["query"])}: {entry["status"]}, '
                f'{len(entry.get("results", []))} candidates',
                file=sys.stderr,
                flush=True,
            )

    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

"""Search unique MSIDs or explicit queries, streaming resumable JSONL results."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import httpx

from lb_mapper import mb_search
from lb_mapper.cli import read_json, repair_jsonl, write_record
from lb_mapper.lb_search import search_recording
from lb_mapper.review import group_listens, uuid_string


def _query(item: dict[str, Any], source: str) -> str:
    if source == 'labs':
        return f'{item["artist"]} {item["track"]}'.strip()
    if item.get('recording_mbid'):
        return uuid_string(item['recording_mbid'])
    return item.get('query') or mb_search.recording_query(item['artist'], item['track'])


def _search_one(item: dict[str, Any], source: str) -> dict[str, Any]:
    query = _query(item, source)
    operation = (
        'lookup' if source == 'musicbrainz' and item.get('recording_mbid') else 'search'
    )
    entry = {**item, 'source': source, 'operation': operation, 'query': query}
    try:
        if source == 'labs':
            results = search_recording(item['artist'], item['track'])
        elif item.get('recording_mbid'):
            results = [mb_search.lookup_recording(query)]
        else:
            results = mb_search.search_recordings(query)
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
    data = read_json(args.input)
    items = group_listens(data['unlinked']) if isinstance(data, dict) else data
    if not isinstance(items, list):
        parser.error('input must be a listen snapshot or an array of queries')
    keys = [
        (
            item.get('recording_msid'),
            'lookup'
            if args.source == 'musicbrainz' and item.get('recording_mbid')
            else 'search',
            _query(item, args.source),
        )
        for item in items
    ]
    completed = set()
    cached: dict[tuple[str, str], list[dict[str, Any]]] = {}
    if args.output and args.output.exists():
        repair_jsonl(args.output)
        with args.output.open() as stream:
            for line in stream:
                row = json.loads(line)
                if row['source'] == args.source and row['status'] == 'ok':
                    operation = row.get('operation') or (
                        'lookup'
                        if args.source == 'musicbrainz' and row.get('recording_mbid')
                        else 'search'
                    )
                    completed.add((row.get('recording_msid'), operation, row['query']))
                    cached[operation, row['query']] = row['results']
    failed = False
    context = args.output.open('a') if args.output else nullcontext(sys.stdout)
    with context as stream:
        for i, (item, key) in enumerate(zip(items, keys, strict=True), 1):
            if key in completed:
                continue
            cache_key = (key[1], key[2])
            if cache_key in cached:
                entry = {
                    **item,
                    'source': args.source,
                    'operation': key[1],
                    'query': key[2],
                    'status': 'ok',
                    'results': cached[cache_key],
                }
            else:
                entry = _search_one(item, args.source)
            if entry['status'] == 'ok':
                completed.add(key)
                cached[cache_key] = entry['results']
            write_record(entry, stream)
            failed |= entry['status'] == 'error'
            print(
                f'[{i}/{len(items)}] {item.get("artist", "")} / '
                f'{item.get("track", entry["query"])}: {entry["status"]}, '
                f'{len(entry.get("results", []))} candidates',
                file=sys.stderr,
                flush=True,
            )
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

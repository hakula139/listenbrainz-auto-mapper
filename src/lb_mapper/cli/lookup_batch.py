"""Look up original artist / title pairs in batches, retaining raw candidates."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from itertools import batched
from pathlib import Path
from typing import Any

import httpx

from lb_mapper.cli import validate_paths
from lb_mapper.cli.search_input import artist_title, read_items
from lb_mapper.cli.search_journal import SearchJournal
from lb_mapper.lb_search import lookup_recordings
from lb_mapper.validation import uuid_string


def _entry(item: dict[str, Any]) -> dict[str, Any]:
    pair = artist_title(item)
    if 'recording_msid' in item:
        uuid_string(item['recording_msid'])

    return {
        **item,
        'source': 'labs-exact',
        'operation': 'lookup',
        'query': json.dumps(pair, ensure_ascii=False),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    validate_paths(parser, args.input, args.output)

    try:
        items = read_items(args.input)
        entries = [_entry(item) for item in items]
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(f'Invalid lookup input: {exc}')

    journal = SearchJournal(args.output, 'labs-exact')
    cached: dict[tuple[str, str], list[dict[str, Any]]] = {}

    queries: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        pair = (entry['artist'], entry['track'])
        if journal.is_complete(entry):
            continue

        outcome = journal.cached(entry)
        if outcome is not None:
            cached[pair] = outcome['results']
        queries.setdefault(pair, []).append(entry)

    failed = False
    context = args.output.open('a') if args.output else nullcontext(sys.stdout)
    with context as stream:
        for batch in batched(queries, 100):
            pending = [pair for pair in batch if pair not in cached]
            error = None
            if pending:
                try:
                    results = lookup_recordings(pending)
                    cached.update(zip(pending, results, strict=True))
                except (httpx.HTTPError, ValueError) as exc:
                    failed = True
                    error = f'{type(exc).__name__}: {exc}'

            for pair in batch:
                for item in queries[pair]:
                    row = _entry(item)
                    if pair in cached:
                        row.update(status='ok', results=cached[pair])
                    else:
                        row.update(status='error', error=error)

                    journal.record(row, stream)

            print(
                f'Looked up {len(batch)} pairs: {error or "ok"}',
                file=sys.stderr,
                flush=True,
            )

    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

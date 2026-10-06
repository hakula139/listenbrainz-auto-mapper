"""Resume completed queries and reuse successful search outcomes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

from lb_mapper.cli import repair_jsonl, write_record
from lb_mapper.review import canonical_lookup


class SearchJournal:
    def __init__(self, path: Path | None, source: str) -> None:
        self.source = source
        self._completed: set[tuple[Any, ...]] = set()
        self._cached: dict[tuple[Any, ...], dict[str, Any]] = {}

        if path is not None and path.exists():
            repair_jsonl(path)
            with path.open() as stream:
                for line in stream:
                    self._remember(json.loads(line))

    def _key(self, row: dict[str, Any]) -> tuple[Any, ...]:
        operation = row.get('operation') or (
            'lookup'
            if row['source'] == 'musicbrainz' and row.get('recording_mbid')
            else 'search'
        )
        return row['source'], operation, row['query'], row.get('offset', 0)

    def _remember(self, row: dict[str, Any]) -> None:
        if row['source'] != self.source or row['status'] != 'ok':
            return

        key = self._key(row)
        if (
            row['source'] == 'musicbrainz'
            and key[1] == 'lookup'
            and canonical_lookup(row) is None
        ):
            return

        if (
            row['source'] == 'musicbrainz'
            and key[1] == 'search'
            and ('result_count' not in row or 'result_offset' not in row)
        ):
            return

        self._completed.add((row.get('recording_msid'), *key))
        self._cached[key] = {
            name: row[name]
            for name in (
                'status',
                'results',
                'result_count',
                'result_offset',
                'next_offset',
            )
            if name in row
        }

    def is_complete(self, row: dict[str, Any]) -> bool:
        return (row.get('recording_msid'), *self._key(row)) in self._completed

    def cached(self, row: dict[str, Any]) -> dict[str, Any] | None:
        return self._cached.get(self._key(row))

    def record(self, row: dict[str, Any], stream: TextIO) -> None:
        write_record(row, stream)
        self._remember(row)

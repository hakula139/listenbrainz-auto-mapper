"""Preflight listen snapshots and explicit search inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lb_mapper.artifacts import read_json
from lb_mapper.review import group_listens


def read_items(path: Path | None) -> list[dict[str, Any]]:
    data = read_json(path)
    items = group_listens(data['unlinked']) if isinstance(data, dict) else data
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError('Input must be a listen snapshot or an array of queries')

    return items


def artist_title(item: dict[str, Any]) -> tuple[str, str]:
    for key in ('artist', 'track'):
        if not isinstance(item.get(key), str) or not item[key].strip():
            raise ValueError(f'Search input requires a non-empty {key} string')

    return item['artist'], item['track']

"""Read enriched ListenBrainz exports in reverse chronological order."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from lb_mapper.lb_client import Listen


def iter_export(path: Path, user: str) -> Iterator[Listen]:
    with ZipFile(path) as archive:
        account = json.loads(archive.read('user.json'))
        if account['username'] != user:
            raise ValueError('Export does not belong to LB_USER')
        months = []
        for name in archive.namelist():
            parts = PurePosixPath(name).parts
            if len(parts) == 3 and parts[0] == 'listens' and name.endswith('.jsonl'):
                months.append((int(parts[1]), int(PurePosixPath(parts[2]).stem), name))
        for _, _, name in sorted(months, reverse=True):
            rows = archive.read(name).splitlines()
            for line in reversed(rows):
                item = json.loads(line)
                timestamp = item['listened_at']
                if type(timestamp) not in (int, float) or timestamp != int(timestamp):
                    raise ValueError('Export listen time must be an integral timestamp')
                item['listened_at'] = int(timestamp)
                yield Listen.from_api(item)

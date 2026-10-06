"""Atomic JSON snapshots and append-only JSONL evidence."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, TextIO


@contextmanager
def atomic_path(path: Path) -> Iterator[Path]:
    """Replace the destination only after its temporary file is complete."""
    with NamedTemporaryFile(
        dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp', delete=False
    ) as temporary:
        staged = Path(temporary.name)

    try:
        yield staged
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def read_json(path: Path | None) -> Any:
    if path is None:
        return json.load(sys.stdin)

    with path.open() as stream:
        return json.load(stream)


def write_json(data: Any, path: Path | None) -> None:
    if path is None:
        json.dump(data, sys.stdout, ensure_ascii=False)
        print()
    else:
        with atomic_path(path) as temporary:
            temporary.write_text(json.dumps(data, ensure_ascii=False) + '\n')


def write_record(data: Any, stream: TextIO) -> None:
    print(json.dumps(data, ensure_ascii=False), file=stream, flush=True)


def repair_jsonl(path: Path) -> None:
    """Repair an interrupted final record while rejecting malformed interior rows."""
    with path.open('r+b') as stream:
        start = 0
        while line := stream.readline():
            end = stream.tell()

            try:
                json.loads(line)
            except (ValueError, UnicodeDecodeError):
                if line.endswith(b'\n') or stream.read(1):
                    raise ValueError('Malformed completed JSONL record') from None
                stream.truncate(start)
                return

            start = end
            if not line.endswith(b'\n'):
                stream.write(b'\n')

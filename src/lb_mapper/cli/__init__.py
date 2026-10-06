"""CLI helpers invoked by the /map-listens skill."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO


def require_env(name: str) -> str:
    """Return the value of env var *name*, or exit with an error."""
    value = os.environ.get(name, '')
    if not value:
        print(f'{name} not set', file=sys.stderr)
        sys.exit(1)
    return value


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
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False) + '\n')
        temporary.replace(path)


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

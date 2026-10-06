"""CLI helpers invoked by the /map-listens skill."""

from __future__ import annotations

import argparse
import os
import sys
from itertools import combinations
from pathlib import Path

from lb_mapper.artifacts import (
    read_json as read_json,
    repair_jsonl as repair_jsonl,
    write_json as write_json,
    write_record as write_record,
)


def validate_paths(parser: argparse.ArgumentParser, *paths: Path | None) -> None:
    """Reject artifact paths that resolve to the same file."""
    resolved = [path.resolve() for path in paths if path is not None]
    if len(resolved) != len(set(resolved)) or any(
        first.exists() and second.exists() and first.samefile(second)
        for first, second in combinations(resolved, 2)
    ):
        parser.error('Input and output artifact paths must be distinct')


def require_env(name: str) -> str:
    """Return the value of env var *name*, or exit with an error."""
    value = os.environ.get(name, '')
    if not value:
        print(f'{name} not set', file=sys.stderr)
        sys.exit(1)

    return value

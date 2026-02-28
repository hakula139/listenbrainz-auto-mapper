"""CLI helpers invoked by the /map-listens skill."""

from __future__ import annotations

import os
import sys


def require_env(name: str) -> str:
    """Return the value of env var *name*, or exit with an error."""
    value = os.environ.get(name, '')
    if not value:
        print(f'{name} not set', file=sys.stderr)
        sys.exit(1)
    return value

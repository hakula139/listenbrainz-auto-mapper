"""ListenBrainz auto-mapper — thin API wrappers for the /map-listens skill."""

from __future__ import annotations

import os
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env() -> None:
    """Load variables from the repo-root .env file into os.environ.

    Only sets variables not already present in the environment.
    Handles blank lines, comments, and optionally quoted values.
    """
    env_path = _REPO_ROOT / '.env'
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        # Strip matching quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ.setdefault(key, value)

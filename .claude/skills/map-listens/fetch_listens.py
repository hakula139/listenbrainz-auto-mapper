"""Fetch recent listens and output unlinked ones as JSON.

Usage:
    uv run python .claude/skills/map-listens/fetch_listens.py [count]

Outputs JSON to stdout:
    {"total": N, "linked": N, "unlinked": [{listened_at, recording_msid, artist, track, release}, ...]}
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, 'src')

from lb_mapper.lb_client import ListenBrainzClient  # noqa: E402


def _load_env() -> None:
    """Load .env file into os.environ (no external dependency)."""
    env_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env')
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    _load_env()

    token = os.environ.get('LB_TOKEN', '')
    if not token:
        print('LB_TOKEN not set', file=sys.stderr)
        sys.exit(1)

    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1000

    with ListenBrainzClient(token) as lb:
        listens = lb.fetch_listens('Hakula', count=count)
        unlinked = [l for l in listens if not l.is_linked]

        out = [
            {
                'listened_at': l.listened_at,
                'recording_msid': l.recording_msid,
                'artist': l.artist_name,
                'track': l.track_name,
                'release': l.release_name,
            }
            for l in unlinked
        ]

        json.dump(
            {
                'total': len(listens),
                'linked': len(listens) - len(unlinked),
                'unlinked': out,
            },
            sys.stdout,
            ensure_ascii=False,
        )

    print(
        f'Fetched {len(listens)}, {len(listens) - len(unlinked)} linked, '
        f'{len(unlinked)} unlinked',
        file=sys.stderr,
        flush=True,
    )


if __name__ == '__main__':
    main()

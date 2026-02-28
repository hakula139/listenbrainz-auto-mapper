"""Fetch recent listens and output unlinked ones as JSON.

Usage:
    uv run python -m lb_mapper.cli.fetch_listens [count]

Outputs JSON to stdout:
    {"total": N, "linked": N, "unlinked": [...]}
"""

from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv

from lb_mapper.lb_client import ListenBrainzClient


def main() -> None:
    load_dotenv()

    token = os.environ.get('LB_TOKEN', '')
    if not token:
        print('LB_TOKEN not set', file=sys.stderr)
        sys.exit(1)

    user = os.environ.get('LB_USER', '')
    if not user:
        print('LB_USER not set', file=sys.stderr)
        sys.exit(1)

    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1000

    with ListenBrainzClient(token) as lb:
        listens = lb.fetch_listens(user, count=count)
        unlinked = [x for x in listens if not x.is_linked]

        out = [
            {
                'listened_at': x.listened_at,
                'recording_msid': x.recording_msid,
                'artist': x.artist_name,
                'track': x.track_name,
                'release': x.release_name,
            }
            for x in unlinked
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

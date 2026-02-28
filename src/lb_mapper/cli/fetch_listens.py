"""Fetch recent listens and output unlinked ones as JSON.

Paginates until *count* unlinked listens are found (or history is
exhausted).

Usage:
    uv run python -m lb_mapper.cli.fetch_listens [count]

Outputs JSON to stdout:
    {"total": N, "linked": N, "unlinked": [...]}
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

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

    unlinked: list[dict[str, Any]] = []
    total = 0
    linked = 0

    with ListenBrainzClient(token) as lb:
        for listen in lb.iter_listens(user):
            total += 1
            if listen.is_linked:
                linked += 1
            else:
                unlinked.append(
                    {
                        'listened_at': listen.listened_at,
                        'recording_msid': listen.recording_msid,
                        'artist': listen.artist_name,
                        'track': listen.track_name,
                        'release': listen.release_name,
                    }
                )
                if len(unlinked) >= count:
                    break

    json.dump(
        {
            'total': total,
            'linked': linked,
            'unlinked': unlinked,
        },
        sys.stdout,
        ensure_ascii=False,
    )

    print(
        f'Scanned {total}, {linked} linked, {len(unlinked)} unlinked',
        file=sys.stderr,
        flush=True,
    )


if __name__ == '__main__':
    main()

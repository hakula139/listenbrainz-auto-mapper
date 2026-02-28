"""Execute approved mappings and deletions.

Reads a JSON object from stdin with two arrays:
    {
        "mappings": [{"recording_msid": "...", "recording_mbid": "..."}, ...],
        "deletions": [{"listened_at": 123, "recording_msid": "..."}, ...]
    }

Usage:
    echo '{"mappings": [...], "deletions": [...]}' | \
        uv run python -m lb_mapper.cli.execute
"""

from __future__ import annotations

import json
import os
import sys

import httpx
from dotenv import load_dotenv

from lb_mapper.lb_client import ListenBrainzClient


def main() -> None:
    load_dotenv()

    token = os.environ.get('LB_TOKEN', '')
    if not token:
        print('LB_TOKEN not set', file=sys.stderr)
        sys.exit(1)

    data = json.loads(sys.stdin.read())
    mappings = data.get('mappings', [])
    deletions = data.get('deletions', [])

    mapped_ok = 0
    deleted_ok = 0

    with ListenBrainzClient(token) as lb:
        if mappings:
            print(
                f'Submitting {len(mappings)} mappings...',
                file=sys.stderr,
                flush=True,
            )
            for i, m in enumerate(mappings, 1):
                try:
                    msid = m['recording_msid'][:12]
                    lb.submit_mapping(m['recording_msid'], m['recording_mbid'])
                    print(
                        f'  [{i}/{len(mappings)}] MAPPED {msid}...',
                        file=sys.stderr,
                        flush=True,
                    )
                    mapped_ok += 1
                except (httpx.HTTPError, KeyError) as exc:
                    print(
                        f'  [{i}/{len(mappings)}] ERROR: {type(exc).__name__}: {exc}',
                        file=sys.stderr,
                        flush=True,
                    )

        if deletions:
            print(
                f'Deleting {len(deletions)} listens...',
                file=sys.stderr,
                flush=True,
            )
            for i, d in enumerate(deletions, 1):
                try:
                    msid = d['recording_msid'][:12]
                    lb.delete_listen(d['listened_at'], d['recording_msid'])
                    print(
                        f'  [{i}/{len(deletions)}] DELETED {msid}...',
                        file=sys.stderr,
                        flush=True,
                    )
                    deleted_ok += 1
                except (httpx.HTTPError, KeyError) as exc:
                    print(
                        f'  [{i}/{len(deletions)}] ERROR: {type(exc).__name__}: {exc}',
                        file=sys.stderr,
                        flush=True,
                    )

    print(
        f'Done: {mapped_ok}/{len(mappings)} mapped, '
        f'{deleted_ok}/{len(deletions)} deleted.',
        file=sys.stderr,
        flush=True,
    )


if __name__ == '__main__':
    main()

"""Execute approved mappings and deletions.

Reads a JSON object from stdin with two arrays:
    {
        "mappings": [{"recording_msid": "...", "recording_mbid": "..."}, ...],
        "deletions": [{"listened_at": 123, "recording_msid": "..."}, ...]
    }

Usage:
    echo '{"mappings": [...], "deletions": [...]}' | \
        uv run python .claude/skills/map-listens/execute.py
"""

from __future__ import annotations

import json
import os
import sys

from lb_mapper import load_env
from lb_mapper.lb_client import ListenBrainzClient


def main() -> None:
    load_env()

    token = os.environ.get('LB_TOKEN', '')
    if not token:
        print('LB_TOKEN not set', file=sys.stderr)
        sys.exit(1)

    data = json.loads(sys.stdin.read())
    mappings = data.get('mappings', [])
    deletions = data.get('deletions', [])

    with ListenBrainzClient(token) as lb:
        if mappings:
            print(f'Submitting {len(mappings)} mappings...', flush=True)
            for i, m in enumerate(mappings, 1):
                msid = m['recording_msid'][:12]
                try:
                    lb.submit_mapping(m['recording_msid'], m['recording_mbid'])
                    print(f'  [{i}/{len(mappings)}] MAPPED {msid}...', flush=True)
                except Exception as exc:
                    print(
                        f'  [{i}/{len(mappings)}] ERROR {msid}...: {exc}',
                        flush=True,
                    )

        if deletions:
            print(f'Deleting {len(deletions)} listens...', flush=True)
            for i, d in enumerate(deletions, 1):
                msid = d['recording_msid'][:12]
                try:
                    lb.delete_listen(d['listened_at'], d['recording_msid'])
                    print(
                        f'  [{i}/{len(deletions)}] DELETED {msid}...',
                        flush=True,
                    )
                except Exception as exc:
                    print(
                        f'  [{i}/{len(deletions)}] ERROR {msid}...: {exc}',
                        flush=True,
                    )

    print(
        f'Done: {len(mappings)} mapped, {len(deletions)} deleted.',
        flush=True,
    )


if __name__ == '__main__':
    main()

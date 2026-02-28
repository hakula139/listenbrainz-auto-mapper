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

sys.path.insert(0, 'src')

from lb_mapper.lb_client import ListenBrainzClient  # noqa: E402


def _load_env() -> None:
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

    data = json.loads(sys.stdin.read())
    mappings = data.get('mappings', [])
    deletions = data.get('deletions', [])

    with ListenBrainzClient(token) as lb:
        if mappings:
            print(f'Submitting {len(mappings)} mappings...', flush=True)
            for i, m in enumerate(mappings, 1):
                try:
                    lb.submit_mapping(m['recording_msid'], m['recording_mbid'])
                    print(f'  [{i}/{len(mappings)}] MAPPED {m["recording_msid"][:12]}...', flush=True)
                except Exception as e:
                    print(f'  [{i}/{len(mappings)}] ERROR {m["recording_msid"][:12]}...: {e}', flush=True)

        if deletions:
            print(f'Deleting {len(deletions)} listens...', flush=True)
            for i, d in enumerate(deletions, 1):
                try:
                    lb.delete_listen(d['listened_at'], d['recording_msid'])
                    print(f'  [{i}/{len(deletions)}] DELETED {d["recording_msid"][:12]}...', flush=True)
                except Exception as e:
                    print(f'  [{i}/{len(deletions)}] ERROR {d["recording_msid"][:12]}...: {e}', flush=True)

    print(f'Done: {len(mappings)} mapped, {len(deletions)} deleted.', flush=True)


if __name__ == '__main__':
    main()

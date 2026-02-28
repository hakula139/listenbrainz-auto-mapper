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
import sys
from collections.abc import Callable
from typing import Any

import httpx
from dotenv import load_dotenv

from lb_mapper.cli import require_env
from lb_mapper.lb_client import ListenBrainzClient


_MSID_DISPLAY_LEN = 12


def _apply_batch(
    items: list[dict[str, Any]],
    action: Callable[[dict[str, Any]], None],
    verb: str,
) -> int:
    """Apply *action* to each item, printing progress. Return success count."""
    ok = 0
    for i, item in enumerate(items, 1):
        try:
            msid = item['recording_msid'][:_MSID_DISPLAY_LEN]
            action(item)
            print(
                f'  [{i}/{len(items)}] {verb} {msid}...',
                file=sys.stderr,
                flush=True,
            )
            ok += 1
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            print(
                f'  [{i}/{len(items)}] ERROR: {type(exc).__name__}: {exc}',
                file=sys.stderr,
                flush=True,
            )
    return ok


def main() -> None:
    load_dotenv()
    token = require_env('LB_TOKEN')

    data = json.loads(sys.stdin.read())
    mappings: list[dict[str, Any]] = data.get('mappings', [])
    deletions: list[dict[str, Any]] = data.get('deletions', [])

    mapped_ok = 0
    deleted_ok = 0

    with ListenBrainzClient(token) as lb:
        if mappings:
            print(
                f'Submitting {len(mappings)} mappings...',
                file=sys.stderr,
                flush=True,
            )
            mapped_ok = _apply_batch(
                mappings,
                action=lambda m: lb.submit_mapping(
                    m['recording_msid'], m['recording_mbid']
                ),
                verb='MAPPED',
            )

        if deletions:
            print(
                f'Deleting {len(deletions)} listens...',
                file=sys.stderr,
                flush=True,
            )
            deleted_ok = _apply_batch(
                deletions,
                action=lambda d: lb.delete_listen(
                    d['listened_at'], d['recording_msid']
                ),
                verb='DELETED',
            )

    print(
        f'Done: {mapped_ok}/{len(mappings)} mapped, '
        f'{deleted_ok}/{len(deletions)} deleted.',
        file=sys.stderr,
        flush=True,
    )


if __name__ == '__main__':
    main()

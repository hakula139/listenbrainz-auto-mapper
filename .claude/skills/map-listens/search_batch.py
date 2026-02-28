"""Batch search LB Labs for recording matches.

Reads a JSON array of {artist, track, release, original_artist} objects
from stdin.  For each, searches LB Labs (Typesense) and returns structured
results.

If ``original_artist`` contains CJK characters and the translated-name
search returns no results, the script retries with the original name.

Usage:
    echo '[...]' | uv run python .claude/skills/map-listens/search_batch.py
"""

from __future__ import annotations

import json
import sys
from typing import Any

from lb_mapper.lb_search import LBRecordingMatch, contains_cjk, search_recording


def _match_to_dict(m: LBRecordingMatch) -> dict[str, Any]:
    return {
        'recording_mbid': m.recording_mbid,
        'recording_name': m.recording_name,
        'release_name': m.release_name,
        'release_mbid': m.release_mbid,
        'artist_credit_name': m.artist_credit_name,
    }


def search_one(item: dict[str, str]) -> dict[str, Any]:
    artist = item.get('artist', '')
    track = item.get('track', '')
    original_artist = item.get('original_artist', '')

    results = search_recording(artist=artist, recording=track)

    # Retry with the original CJK name if translation yielded nothing.
    if not results and original_artist and contains_cjk(original_artist):
        results = search_recording(artist=original_artist, recording=track)

    return {
        **item,
        'results': [_match_to_dict(m) for m in results],
    }


def main() -> None:
    items: list[dict[str, str]] = json.loads(sys.stdin.read())

    output: list[dict[str, Any]] = []
    for i, item in enumerate(items, 1):
        entry = search_one(item)
        output.append(entry)
        print(
            f'[{i}/{len(items)}] '
            f'{item.get("artist", "")} — {item.get("track", "")} '
            f'-> {len(entry["results"])} results',
            file=sys.stderr,
            flush=True,
        )

    json.dump(output, sys.stdout, ensure_ascii=False)


if __name__ == '__main__':
    main()

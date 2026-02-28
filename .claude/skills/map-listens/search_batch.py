"""Batch search LB Labs for recording matches.

Reads a JSON array of {artist, track, release} objects from stdin.
For each, searches LB Labs (Typesense) and returns structured results.

If the artist field appears to be a translated name and the original
contains CJK characters, the script searches with the translated name
first, then retries with the original if no results are found.

Usage:
    echo '[...]' | uv run python .claude/skills/map-listens/search_batch.py
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any


sys.path.insert(0, 'src')

from lb_mapper.lb_search import (  # noqa: E402
    LBRecordingMatch,
    search_recording,
)


_CJK_PATTERN = re.compile(r'[\u3000-\u9fff]')


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_PATTERN.search(text))


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

    # If the provided artist is a translation (original is CJK), and we got
    # no results, retry with the original CJK name.
    if not results and original_artist and _contains_cjk(original_artist):
        results = search_recording(artist=original_artist, recording=track)

    return {
        **item,
        'results': [_match_to_dict(m) for m in results],
    }


def main() -> None:
    raw = sys.stdin.read()
    items: list[dict[str, str]] = json.loads(raw)

    output: list[dict[str, Any]] = []
    for i, item in enumerate(items, 1):
        entry = search_one(item)
        output.append(entry)
        print(
            f'[{i}/{len(items)}] {item.get("artist", "")} — {item.get("track", "")} '
            f'-> {len(entry["results"])} results',
            file=sys.stderr,
            flush=True,
        )

    json.dump(output, sys.stdout, ensure_ascii=False)


if __name__ == '__main__':
    main()

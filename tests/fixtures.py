"""Shared listen payloads and identifiers for contract tests."""

from __future__ import annotations


MSID = '00000000-0000-0000-0000-000000000001'
OTHER_MSID = '00000000-0000-0000-0000-000000000002'
MBID = '10000000-0000-0000-0000-000000000001'
OTHER_MBID = '10000000-0000-0000-0000-000000000002'
TIMESTAMP = 1700000000


def api_listen(timestamp: int | float, index: int) -> dict:
    return {
        'listened_at': timestamp,
        'recording_msid': f'00000000-0000-0000-0000-{index:012d}',
        'track_metadata': {'artist_name': 'Artist', 'track_name': f'Track {index}'},
    }


def occurrence(timestamp: int | str = TIMESTAMP, msid: str = MSID) -> dict:
    return {
        'listened_at': timestamp,
        'recording_msid': msid,
        'artist': 'Artist',
        'track': 'Title',
        'release': 'Release',
    }


def recording(mbid: str = MBID) -> dict:
    return {
        'id': mbid,
        'title': 'Title',
        'video': False,
        'artist-credit': [{'name': 'Artist'}],
        'releases': [],
        'isrcs': [],
        'relations': [],
    }

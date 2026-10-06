"""Group listen occurrences and validate complete review decisions."""

from __future__ import annotations

from typing import Any
from uuid import UUID


def uuid_string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError('Identifier must be a UUID string')
    return str(UUID(value))


def group_listens(listens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for listen in listens:
        msid = uuid_string(listen['recording_msid'])
        if msid not in groups:
            groups[msid] = {
                'recording_msid': msid,
                'artist': listen['artist'],
                'track': listen['track'],
                'release': listen['release'],
                'listens': [],
            }
        groups[msid]['listens'].append(listen)
    return list(groups.values())


def prepare_actions(
    snapshot: dict[str, Any], decisions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Require one evidenced verdict per MSID before constructing actions."""
    targets = {t['recording_msid']: t for t in group_listens(snapshot['unlinked'])}
    reviewed: set[str] = set()
    mappings: list[dict[str, Any]] = []
    deletions: list[dict[str, Any]] = []
    for decision in decisions:
        msid = uuid_string(decision['recording_msid'])
        if msid not in targets or msid in reviewed:
            raise ValueError('Review contains an unknown or duplicate MSID')
        reviewed.add(msid)
        if not decision.get('reason') or not decision.get('evidence'):
            raise ValueError('Each verdict needs a reason and search evidence')
        verdict = decision['verdict']
        if verdict in ('link', 'substitute'):
            mappings.append(
                {
                    'recording_msid': msid,
                    'recording_mbid': uuid_string(decision['recording_mbid']),
                }
            )
        elif verdict == 'delete':
            if decision.get('search_complete') is not True:
                raise ValueError(
                    'Deletion requires a completed search and final review'
                )
            deletions.extend(
                {
                    'listened_at': listen['listened_at'],
                    'recording_msid': msid,
                }
                for listen in targets[msid]['listens']
            )
        elif verdict != 'skip':
            raise ValueError(f'Unknown verdict: {verdict}')
    if reviewed != targets.keys():
        raise ValueError('Review is incomplete: some MSIDs have no verdict')
    return {'user': snapshot['user'], 'mappings': mappings, 'deletions': deletions}

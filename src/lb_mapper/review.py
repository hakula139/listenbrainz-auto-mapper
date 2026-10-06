"""Group listen occurrences and validate complete review decisions."""

from __future__ import annotations

from typing import Any
from uuid import UUID


def uuid_string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError('Identifier must be a UUID string')
    return str(UUID(value))


def validate_actions(data: Any) -> dict[str, Any]:
    """Validate the entire batch before any authenticated operation."""
    if not isinstance(data, dict) or not isinstance(data.get('user'), str):
        raise ValueError('Action plan must contain a user')
    if not data['user'].strip():
        raise ValueError('Action plan user must not be empty')

    mappings: dict[str, dict[str, str]] = {}
    deletions: dict[tuple[int, str], dict[str, Any]] = {}

    for key in ('mappings', 'deletions'):
        items = data.get(key, [])
        if not isinstance(items, list):
            raise ValueError(f'{key} must be an array')

        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f'{key} items must be objects')

            msid = uuid_string(item['recording_msid'])
            if key == 'mappings':
                mapping = {
                    'recording_msid': msid,
                    'recording_mbid': uuid_string(item['recording_mbid']),
                }
                if 'previous_recording_mbid' in item:
                    mapping['previous_recording_mbid'] = uuid_string(
                        item['previous_recording_mbid']
                    )

                if msid in mappings and mappings[msid] != mapping:
                    raise ValueError('Conflicting mappings for one MSID')

                mappings[msid] = mapping
            else:
                timestamp = item['listened_at']
                if type(timestamp) is not int or timestamp < 1033410600:
                    raise ValueError('Deletion timestamp must be a valid listen time')

                deletions[timestamp, msid] = {
                    'listened_at': timestamp,
                    'recording_msid': msid,
                }

    if any(msid in mappings for _, msid in deletions):
        raise ValueError('Cannot map and delete the same MSID in one plan')

    return {
        'user': data['user'],
        'mappings': list(mappings.values()),
        'deletions': list(deletions.values()),
    }


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
            mapping = {
                'recording_msid': msid,
                'recording_mbid': decision['recording_mbid'],
            }
            if 'previous_recording_mbid' in decision:
                mapping['previous_recording_mbid'] = decision['previous_recording_mbid']

            mappings.append(mapping)
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

    return validate_actions(
        {'user': snapshot['user'], 'mappings': mappings, 'deletions': deletions}
    )

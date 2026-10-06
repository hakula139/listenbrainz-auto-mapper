"""Apply reviewed actions and observe their current account state."""

from __future__ import annotations

from lb_mapper.lb_client import ListenBrainzClient
from lb_mapper.review import DeletionAction, MappingAction


def apply_mapping(lb: ListenBrainzClient, item: MappingAction) -> str:
    current = lb.get_manual_mapping(item['recording_msid'])
    if current == item['recording_mbid']:
        return 'unchanged'
    if current is not None and current != item.get('previous_recording_mbid'):
        raise ValueError('A different manual mapping exists. Re-review this MSID')

    lb.submit_mapping(item['recording_msid'], item['recording_mbid'])
    if lb.get_manual_mapping(item['recording_msid']) != item['recording_mbid']:
        raise RuntimeError('Submitted mapping has not been confirmed by readback')

    return 'mapped'


def apply_deletion(lb: ListenBrainzClient, user: str, item: DeletionAction) -> str:
    listen = lb.get_listen(user, item['listened_at'], item['recording_msid'])
    if listen is None:
        return 'absent'
    if listen.is_linked or lb.get_manual_mapping(item['recording_msid']) is not None:
        raise ValueError('Listen is now linked. Re-review before deleting it')

    lb.delete_listen(item['listened_at'], item['recording_msid'])
    return 'scheduled'


def verify_mapping(
    lb: ListenBrainzClient, item: MappingAction
) -> dict[str, str | None]:
    mbid = lb.get_manual_mapping(item['recording_msid'])
    return {
        'status': 'verified' if mbid == item['recording_mbid'] else 'mismatch',
        'actual_recording_mbid': mbid,
    }


def verify_deletion(lb: ListenBrainzClient, user: str, item: DeletionAction) -> str:
    listen = lb.get_listen(user, item['listened_at'], item['recording_msid'])
    return 'absent' if listen is None else 'pending'

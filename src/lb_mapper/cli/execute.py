"""Validate a reviewed plan, then apply it with --apply and stream JSONL outcomes."""

from __future__ import annotations

import argparse
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any, TextIO

import httpx
from dotenv import load_dotenv

from lb_mapper.cli import (
    read_json,
    repair_jsonl,
    require_env,
    validate_paths,
    write_json,
    write_record,
)
from lb_mapper.lb_client import ListenBrainzClient
from lb_mapper.review import validate_actions


def apply_mapping(lb: ListenBrainzClient, item: dict[str, Any]) -> str:
    current = lb.get_manual_mapping(item['recording_msid'])
    if current == item['recording_mbid']:
        return 'unchanged'
    if current is not None:
        raise ValueError('A different manual mapping exists. Re-review this MSID')

    lb.submit_mapping(item['recording_msid'], item['recording_mbid'])
    if lb.get_manual_mapping(item['recording_msid']) != item['recording_mbid']:
        raise RuntimeError('Submitted mapping has not been confirmed by readback')

    return 'mapped'


def apply_deletion(lb: ListenBrainzClient, user: str, item: dict[str, Any]) -> str:
    listen = lb.get_listen(user, item['listened_at'], item['recording_msid'])
    if listen is None:
        return 'absent'
    if listen.is_linked or lb.get_manual_mapping(item['recording_msid']) is not None:
        raise ValueError('Listen is now linked. Re-review before deleting it')

    lb.delete_listen(item['listened_at'], item['recording_msid'])
    return 'scheduled'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    validate_paths(parser, args.input, args.output)

    try:
        data = validate_actions(read_json(args.input))
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))

    if not args.apply:
        write_json(data, args.output)
        return

    load_dotenv()
    user = require_env('LB_USER')
    if user != data['user']:
        parser.error('Plan user does not match LB_USER')

    token = require_env('LB_TOKEN')

    with ListenBrainzClient(token) as lb:
        lb.validate_token(user)

        if args.output and args.output.exists():
            repair_jsonl(args.output)

        context = args.output.open('a') if args.output else nullcontext(sys.stdout)
        with context as stream:
            _execute(lb, user, data, stream)


def _execute(
    lb: ListenBrainzClient, user: str, data: dict[str, Any], stream: TextIO
) -> None:
    failed = False

    for action, items in (
        ('mapping', data['mappings']),
        ('deletion', data['deletions']),
    ):
        for i, item in enumerate(items, 1):
            record = {'action': action, **item}

            try:
                record['status'] = (
                    apply_mapping(lb, item)
                    if action == 'mapping'
                    else apply_deletion(lb, user, item)
                )
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                failed = True
                record.update(status='error', error=f'{type(exc).__name__}: {exc}')

            write_record(record, stream)
            print(
                f'[{i}/{len(items)}] {action}: {record["status"]}',
                file=sys.stderr,
                flush=True,
            )

    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

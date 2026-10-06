"""Read back mappings and check whether scheduled deletions have landed."""

from __future__ import annotations

import argparse
import sys
from contextlib import nullcontext
from pathlib import Path

import httpx
from dotenv import load_dotenv

from lb_mapper.cli import read_json, require_env, write_record
from lb_mapper.cli.execute import validate_actions
from lb_mapper.lb_client import ListenBrainzClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    data = validate_actions(read_json(args.input))
    load_dotenv()
    user = require_env('LB_USER')
    if user != data['user']:
        parser.error('Plan user does not match LB_USER')
    failed = False
    pending = False
    context = args.output.open('w') if args.output else nullcontext(sys.stdout)
    with ListenBrainzClient(require_env('LB_TOKEN')) as lb, context as stream:
        lb.validate_token(user)
        for action, items in (
            ('mapping', data['mappings']),
            ('deletion', data['deletions']),
        ):
            for item in items:
                record = {'action': action, **item}
                try:
                    if action == 'mapping':
                        mbid = lb.get_manual_mapping(item['recording_msid'])
                        record['status'] = (
                            'verified' if mbid == item['recording_mbid'] else 'mismatch'
                        )
                        record['actual_recording_mbid'] = mbid
                        failed |= record['status'] == 'mismatch'
                    else:
                        listen = lb.get_listen(
                            user, item['listened_at'], item['recording_msid']
                        )
                        record['status'] = 'absent' if listen is None else 'pending'
                        pending |= listen is not None
                except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                    failed = True
                    record.update(status='error', error=f'{type(exc).__name__}: {exc}')
                write_record(record, stream)
    if failed or pending:
        print('Verification has errors or pending deletions', file=sys.stderr)
        raise SystemExit(1 if failed else 2)


if __name__ == '__main__':
    main()

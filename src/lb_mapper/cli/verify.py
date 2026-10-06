"""Read back mappings and check whether scheduled deletions have landed."""

from __future__ import annotations

import argparse
import sys
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
from dotenv import load_dotenv

from lb_mapper.cli import (
    read_json,
    repair_jsonl,
    require_env,
    validate_paths,
    write_record,
)
from lb_mapper.execution import verify_deletion, verify_mapping
from lb_mapper.lb_client import ListenBrainzClient
from lb_mapper.review import DeletionAction, MappingAction, validate_actions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    validate_paths(parser, args.input, args.output)

    try:
        data = validate_actions(read_json(args.input))
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))

    load_dotenv()
    user = require_env('LB_USER')
    if user != data['user']:
        parser.error('Plan user does not match LB_USER')

    failed = False
    pending = False

    with ListenBrainzClient(require_env('LB_TOKEN')) as lb:
        lb.validate_token(user)
        if args.output and args.output.exists():
            repair_jsonl(args.output)

        context = args.output.open('a') if args.output else nullcontext(sys.stdout)

        with context as stream:
            for action, items in (
                ('mapping', data['mappings']),
                ('deletion', data['deletions']),
            ):
                for item in items:
                    record: dict[str, Any] = {
                        'action': action,
                        **item,
                        'observed_at': datetime.now(UTC).isoformat(),
                    }

                    try:
                        if action == 'mapping':
                            record.update(verify_mapping(lb, cast(MappingAction, item)))
                            failed |= record['status'] == 'mismatch'
                        else:
                            record['status'] = verify_deletion(
                                lb, user, cast(DeletionAction, item)
                            )
                            pending |= record['status'] == 'pending'
                    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                        failed = True
                        record.update(
                            status='error', error=f'{type(exc).__name__}: {exc}'
                        )

                    write_record(record, stream)

    if failed or pending:
        print('Verification has errors or pending deletions', file=sys.stderr)
        raise SystemExit(1 if failed else 2)


if __name__ == '__main__':
    main()

"""Collect recent unlinked listens, preserving submitted metadata."""

from __future__ import annotations

import argparse
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from lb_mapper.cli import require_env, write_json
from lb_mapper.history import iter_export
from lb_mapper.lb_client import ListenBrainzClient
from lb_mapper.review import uuid_string


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('count', type=int, nargs='?', default=100)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--export', dest='archive', type=Path)
    args = parser.parse_args()
    if args.count < 1:
        parser.error('count must be positive')

    load_dotenv()
    user = require_env('LB_USER')
    unlinked: list[dict[str, Any]] = []
    total = 0
    linked = 0

    with ExitStack() as stack:
        if args.archive:
            listens = iter_export(args.archive, user)
        else:
            lb = stack.enter_context(ListenBrainzClient(require_env('LB_TOKEN')))
            lb.validate_token(user)
            listens = lb.iter_listens(user)
        for listen in listens:
            total += 1
            if listen.is_linked:
                linked += 1
            else:
                unlinked.append(
                    {
                        'listened_at': listen.listened_at,
                        'recording_msid': uuid_string(listen.recording_msid),
                        'artist': listen.artist_name,
                        'track': listen.track_name,
                        'release': listen.release_name,
                        'additional_info': listen.additional_info,
                        'mbid_mapping': listen.mbid_mapping,
                    }
                )
                if len(unlinked) >= args.count:
                    break
            if total % 1000 == 0:
                print(
                    f'Scanned {total}, found {len(unlinked)} unlinked',
                    file=sys.stderr,
                    flush=True,
                )

    write_json(
        {
            'user': user,
            'requested': args.count,
            'total': total,
            'linked': linked,
            'unlinked': unlinked,
        },
        args.output,
    )
    print(
        f'Scanned {total}, {linked} linked, {len(unlinked)} unlinked',
        file=sys.stderr,
        flush=True,
    )


if __name__ == '__main__':
    main()

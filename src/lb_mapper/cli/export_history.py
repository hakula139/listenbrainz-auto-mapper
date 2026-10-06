"""Request or resume one full-history export and stream its archive to disk."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from lb_mapper.cli import read_json, require_env, validate_paths, write_json
from lb_mapper.lb_client import ListenBrainzClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    state_path = args.output.with_suffix(args.output.suffix + '.json')
    validate_paths(parser, args.output, state_path)

    saved_path = state_path if state_path.exists() else None
    legacy_path = args.output.with_suffix('.json')
    if (
        saved_path is None
        and legacy_path.resolve() != args.output.resolve()
        and legacy_path.exists()
    ):
        saved_path = legacy_path

    if args.output.exists() and saved_path is None:
        parser.error('Existing archive has no export state. Choose another output path')

    load_dotenv()
    user = require_env('LB_USER')
    downloaded = False

    with ListenBrainzClient(require_env('LB_TOKEN')) as lb:
        lb.validate_token(user)

        if saved_path is not None:
            saved = read_json(saved_path)
            if saved['user'] != user:
                parser.error('Export state does not belong to LB_USER')
            state = lb.get_export(saved['export']['export_id'])
            downloaded = saved.get('downloaded') is True and args.output.exists()
        else:
            pending = [
                job
                for job in lb.list_exports()
                if job['status'] in ('waiting', 'in_progress')
            ]
            if pending:
                state = pending[0]
                if (
                    state.get('start_time') is not None
                    or state.get('end_time') is not None
                ):
                    parser.error('A ranged export is already pending for this account')
            else:
                state = lb.request_export()

        while True:
            write_json(
                {'user': user, 'export': state, 'downloaded': downloaded}, state_path
            )
            print(
                f'Export {state["export_id"]}: {state["status"]}',
                file=sys.stderr,
                flush=True,
            )

            if state['status'] == 'completed':
                if not downloaded:
                    lb.download_export(state['export_id'], args.output)
                    write_json(
                        {'user': user, 'export': state, 'downloaded': True}, state_path
                    )
                return

            if state['status'] == 'failed':
                raise RuntimeError('ListenBrainz history export failed')
            if state['status'] not in ('waiting', 'in_progress'):
                raise ValueError('Unknown ListenBrainz export status')

            time.sleep(30)
            state = lb.get_export(state['export_id'])


if __name__ == '__main__':
    main()

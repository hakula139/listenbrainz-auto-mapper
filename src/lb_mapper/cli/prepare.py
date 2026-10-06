"""Validate complete MSID decisions and produce a reviewed action plan."""

from __future__ import annotations

import argparse
from pathlib import Path

from lb_mapper.cli import read_json, validate_paths, write_json
from lb_mapper.review import prepare_actions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('decisions', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    validate_paths(parser, args.snapshot, args.decisions, args.output)

    try:
        actions = prepare_actions(read_json(args.snapshot), read_json(args.decisions))
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))

    write_json(actions, args.output)


if __name__ == '__main__':
    main()

"""CLI entry point for lb-mapper."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from lb_mapper.lb_client import ListenBrainzClient
from lb_mapper.mapper import MappingStatus, process_listens
from lb_mapper.mb_search import close_client as close_mb_client
from lb_mapper.translator import Translator


def _load_dotenv() -> None:
    """Load .env file from the project root if it exists."""
    env_file = Path(__file__).resolve().parent.parent.parent / '.env'
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


@click.group()
def cli() -> None:
    """Automatically map unlinked ListenBrainz listens to MusicBrainz recordings."""
    _load_dotenv()


@cli.command('map')
@click.option('--user', required=True, help='ListenBrainz username.')
@click.option(
    '--count',
    default=50,
    show_default=True,
    help='Number of recent listens to process.',
)
@click.option(
    '--max-ts',
    type=int,
    default=None,
    help='Pagination: only fetch listens before this Unix timestamp.',
)
@click.option(
    '--dry-run',
    is_flag=True,
    help='Show what would be mapped without submitting.',
)
def map_listens(user: str, count: int, max_ts: int | None, dry_run: bool) -> None:
    """Fetch recent listens and map unlinked ones to MusicBrainz."""
    lb_token = os.environ.get('LB_TOKEN')
    if not lb_token:
        click.echo('Error: LB_TOKEN environment variable is required.', err=True)
        sys.exit(1)

    try:
        with ListenBrainzClient(lb_token) as lb, Translator() as translator:
            if dry_run:
                click.echo('DRY RUN — no mappings will be submitted.\n')

            results = process_listens(
                lb,
                translator,
                user,
                count=count,
                max_ts=max_ts,
                dry_run=dry_run,
            )

            mapped = 0
            skipped = 0
            no_match = 0
            errors = 0

            for r in results:
                if r.status == MappingStatus.ALREADY_LINKED:
                    skipped += 1
                    continue

                artist_display = r.listen.artist_name
                if r.translated_artist:
                    artist_display = f'{r.listen.artist_name} → {r.translated_artist}'

                if r.status == MappingStatus.MAPPED and r.match:
                    mapped += 1
                    click.echo(
                        f'  ✓ {artist_display}'
                        f' — {r.listen.track_name}\n'
                        f'    → {r.match.artist_credit}'
                        f' — {r.match.title}'
                        f' (score: {r.match.score})'
                    )
                elif r.status == MappingStatus.ERROR:
                    errors += 1
                    click.echo(
                        f'  ! {artist_display} — {r.listen.track_name}'
                        f' (error: {r.error})'
                    )
                elif r.status == MappingStatus.NO_MATCH:
                    no_match += 1
                    click.echo(f'  ✗ {artist_display} — {r.listen.track_name}')

            summary = (
                f'\nDone: {mapped} mapped, {no_match} unmatched,'
                f' {skipped} already linked.'
            )
            if errors:
                summary += f' {errors} errors.'
            click.echo(summary)

    finally:
        close_mb_client()

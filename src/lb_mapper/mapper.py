"""Orchestration: fetch → filter → translate → search → map."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import click

from lb_mapper.lb_client import Listen, ListenBrainzClient
from lb_mapper.mb_search import (
    RecordingMatch,
    find_best_match,
    find_match_by_track_only,
    find_match_by_track_release,
)
from lb_mapper.translator import Translator, contains_cjk


class MappingStatus(Enum):
    ALREADY_LINKED = 'already-linked'
    MAPPED = 'mapped'
    NO_MATCH = 'no-match'
    ERROR = 'error'


@dataclass
class MappingResult:
    listen: Listen
    status: MappingStatus
    match: RecordingMatch | None = None
    translated_artist: str | None = None
    error: str | None = None


def process_listens(
    lb: ListenBrainzClient,
    translator: Translator,
    user: str,
    count: int = 50,
    max_ts: int | None = None,
    dry_run: bool = False,
) -> list[MappingResult]:
    listens = lb.fetch_listens(user, count=count, max_ts=max_ts)
    results: list[MappingResult] = []

    for listen in listens:
        if listen.is_linked:
            results.append(
                MappingResult(
                    listen=listen,
                    status=MappingStatus.ALREADY_LINKED,
                )
            )
            continue

        try:
            result = _try_map(lb, translator, listen, dry_run)
        except Exception as e:
            click.echo(
                f'  ! Error processing {listen.artist_name} — {listen.track_name}: {e}',
                err=True,
            )
            result = MappingResult(
                listen=listen,
                status=MappingStatus.ERROR,
                error=str(e),
            )
        results.append(result)

    return results


def _try_map(
    lb: ListenBrainzClient,
    translator: Translator,
    listen: Listen,
    dry_run: bool,
) -> MappingResult:
    # Step 1: search with original metadata
    match = find_best_match(
        artist=listen.artist_name,
        recording=listen.track_name,
    )
    if match:
        if not dry_run:
            lb.submit_mapping(listen.recording_msid, match.mbid)
        return MappingResult(listen=listen, status=MappingStatus.MAPPED, match=match)

    # Step 2: translate CJK artist name, retry search
    translated_artist: str | None = None
    if contains_cjk(listen.artist_name):
        translated_artist = translator.translate_artist(listen.artist_name)
        match = find_best_match(
            artist=translated_artist,
            recording=listen.track_name,
        )
        if match:
            if not dry_run:
                lb.submit_mapping(listen.recording_msid, match.mbid)
            return MappingResult(
                listen=listen,
                status=MappingStatus.MAPPED,
                match=match,
                translated_artist=translated_artist,
            )

    # Step 3: fallback — search by track + release
    if listen.release_name:
        expected_artist = translated_artist or listen.artist_name
        match = find_match_by_track_release(
            recording=listen.track_name,
            release=listen.release_name,
            expected_artist=expected_artist,
        )
        if match:
            if not dry_run:
                lb.submit_mapping(listen.recording_msid, match.mbid)
            return MappingResult(
                listen=listen,
                status=MappingStatus.MAPPED,
                match=match,
                translated_artist=translated_artist,
            )

    # Step 4: last resort — search by track name only
    expected_artist = translated_artist or listen.artist_name
    match = find_match_by_track_only(
        recording=listen.track_name,
        expected_artist=expected_artist,
    )
    if match:
        if not dry_run:
            lb.submit_mapping(listen.recording_msid, match.mbid)
        return MappingResult(
            listen=listen,
            status=MappingStatus.MAPPED,
            match=match,
            translated_artist=translated_artist,
        )

    # Step 5: no match
    return MappingResult(
        listen=listen,
        status=MappingStatus.NO_MATCH,
        translated_artist=translated_artist,
    )

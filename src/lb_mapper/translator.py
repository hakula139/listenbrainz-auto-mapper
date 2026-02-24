"""CJK detection and LLM-assisted translation of artist names via Codex MCP."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from pathlib import Path
from typing import cast

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent


CACHE_DIR = Path.home() / '.cache' / 'lb-mapper'
CACHE_FILE = CACHE_DIR / 'translations.json'

# CJK Unified Ideographs, Katakana, Hiragana, CJK symbols
_CJK_PATTERN = re.compile(r'[\u3000-\u9fff]')

# Suppress noisy validation warnings from Codex's custom MCP notifications.
# These come from the mcp library's pydantic validation of Codex's non-standard
# notifications (codex/event). We only silence warnings, not errors.
logging.basicConfig(level=logging.ERROR)

_CODEX_SERVER_PARAMS = StdioServerParameters(
    command='codex',
    args=['mcp-server'],
)


def contains_cjk(text: str) -> bool:
    return bool(_CJK_PATTERN.search(text))


class Translator:
    """Translates CJK artist names using a persistent Codex MCP session.

    Uses a background asyncio task to keep the MCP session alive across
    multiple translation calls (avoids anyio task-scoping issues).
    """

    def __init__(self, model: str = 'gpt-5.2') -> None:
        self._model = model
        self._cache = self._load_cache()
        self._loop = asyncio.new_event_loop()
        self._request_queue: asyncio.Queue[tuple[str | None, asyncio.Future[str]]] = (
            asyncio.Queue()
        )
        self._worker_task: asyncio.Task[None] | None = None

    def __enter__(self) -> Translator:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object,
    ) -> None:
        self.close()

    def translate_artist(self, artist_name: str) -> str:
        """Translate a CJK artist name to English. Returns original if not CJK."""
        if not contains_cjk(artist_name):
            return artist_name

        if artist_name in self._cache:
            return self._cache[artist_name]

        translated = self._run_translate(artist_name)
        self._cache[artist_name] = translated
        self._save_cache()
        return translated

    def close(self) -> None:
        if self._worker_task is not None:
            # Signal the worker to stop
            future: asyncio.Future[str] = self._loop.create_future()
            self._loop.call_soon_threadsafe(
                self._request_queue.put_nowait,
                (None, future),
            )
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(self._worker_task)
        self._loop.close()

    def _run_translate(self, artist_name: str) -> str:
        """Send a translation request to the background worker."""
        if self._worker_task is None:
            self._worker_task = self._loop.create_task(self._worker())

        future: asyncio.Future[str] = self._loop.create_future()
        self._request_queue.put_nowait((artist_name, future))
        return self._loop.run_until_complete(future)

    async def _worker(self) -> None:
        """Long-lived task that owns the MCP session."""
        async with (
            stdio_client(_CODEX_SERVER_PARAMS) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()

            while True:
                artist_name, future = await self._request_queue.get()

                # None signals shutdown
                if artist_name is None:
                    future.set_result('')
                    return

                try:
                    result = await self._do_translate(
                        session,
                        artist_name,
                    )
                    future.set_result(result)
                except Exception as e:
                    future.set_exception(e)

    async def _do_translate(self, session: ClientSession, artist_name: str) -> str:
        result = await session.call_tool(
            'codex',
            arguments={
                'prompt': (
                    'Translate the following CJK artist'
                    ' name to its English equivalent.'
                    ' This is a music artist name that may be'
                    ' a katakana transliteration of a'
                    ' Western name, or a native CJK'
                    ' artist name. Return ONLY the English'
                    f' name, nothing else.\n\n{artist_name}'
                ),
                'model': self._model,
                'sandbox': 'read-only',
            },
        )
        for content in result.content:
            if isinstance(content, TextContent):
                return content.text.strip()
        return artist_name

    def _load_cache(self) -> dict[str, str]:
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
            except json.JSONDecodeError, ValueError:
                return {}
            if isinstance(data, dict):
                return cast(dict[str, str], data)
            return {}
        return {}

    def _save_cache(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2), encoding='utf-8'
        )

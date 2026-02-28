"""CJK detection and translation cache for artist names."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast


CACHE_DIR = Path.home() / '.cache' / 'lb-mapper'
CACHE_FILE = CACHE_DIR / 'translations.json'

# CJK Unified Ideographs, Katakana, Hiragana, CJK symbols
_CJK_PATTERN = re.compile(r'[\u3000-\u9fff]')


def contains_cjk(text: str) -> bool:
    return bool(_CJK_PATTERN.search(text))


def _load_cache() -> dict[str, str]:
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, ValueError):
            return {}
        if isinstance(data, dict):
            return cast(dict[str, str], data)
        return {}
    return {}


def save_cache(cache: dict[str, str]) -> None:
    """Persist the translation cache to disk."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8'
    )


def get_cached_translation(artist_name: str) -> str | None:
    """Return the cached English translation for a CJK artist name, or None."""
    cache = _load_cache()
    return cache.get(artist_name)

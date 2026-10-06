"""Identifier validation shared by API responses and reviewed plans."""

from __future__ import annotations

from typing import Any
from uuid import UUID


def uuid_string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError('Identifier must be a UUID string')

    return str(UUID(value))

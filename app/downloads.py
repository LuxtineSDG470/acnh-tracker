"""Portable, timezone-aware filenames for user-initiated downloads."""
from __future__ import annotations

from datetime import datetime


def timestamped_filename(stem: str, extension: str, generated_at: datetime) -> str:
    """Use sortable wall-clock time plus its UTC offset (DST-safe).

    Only application-owned, fixed stems and extensions are passed by callers.
    Unlike colons in ISO timestamps, hyphens work in Windows filenames.
    """
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError('A timezone-aware datetime is required')
    return f"{stem}-{generated_at.strftime('%Y-%m-%d_%H-%M-%S%z')}.{extension}"

"""Safe local-day handling for optional Docker/Portainer TZ settings.

Portainer can resolve ``TZ=${TZ}`` to an empty string if the stack variable
is unset. Python's ZoneInfo rejects an empty key with ValueError, so all
requests that render the shared page context otherwise fail with HTTP 500.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = 'America/Chicago'


def timezone_name(raw: str | None) -> str:
    """Select a usable IANA zone without ever returning an invalid empty key."""
    configured = (raw or '').strip()
    for candidate in (configured, DEFAULT_TIMEZONE, 'UTC'):
        if not candidate:
            continue
        try:
            ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
        return candidate
    # Some stripped-down images might not include system tzdata at all.
    return 'UTC'


def local_date(raw: str | None):
    """Return today's date in configured zone; safely fall back to UTC."""
    key = timezone_name(raw)
    try:
        return datetime.now(ZoneInfo(key)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(timezone.utc).date()


def valid_timezone(raw: str | None) -> bool:
    """Validate explicit per-account IANA timezones (no silent correction)."""
    if not isinstance(raw, str) or not raw or len(raw) > 100 or raw != raw.strip():
        return False
    try:
        ZoneInfo(raw)
        return True
    except (ValueError, ZoneInfoNotFoundError):
        return False

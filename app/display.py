"""Independent display rules for seasonal collections and card detail labels.

The database keeps original catalog values. Presentation-only normalization
happens here so upgrading the interface never rewrites a user's catalog.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

FISH_SHADOW_NAMES = {
    '1': 'Tiny', '2': 'Small', '3': 'Medium', '4': 'Large',
    '5': 'Huge', '6': 'Massive', '4 (Fin)': 'Large (Fin)',
    '6 (Fin)': 'Massive (Fin)', 'Narrow': 'Narrow',
}

# The imported calendar is a 12-character January-to-December bitset.
DIY_TYPE_NAMES = {
    'tool': 'Tool', 'houseware': 'Houseware', 'misc': 'Miscellaneous',
    'wallMounted': 'Wall-mounted', 'wallpaperFlooringRug': 'Wallpaper / Flooring / Rug',
    'equipment': 'Equipment', 'other': 'Other',
}
YEAR_ROUND = '1' * 12
TIME_SPAN = re.compile(r'(\d{1,2})\s*(AM|PM)\s*[-\u2013\u2014]\s*(\d{1,2})\s*(AM|PM)', re.IGNORECASE)


def available_months(metadata: dict[str, Any], hemisphere: str) -> str | None:
    all_months = metadata.get('months')
    months = all_months.get(hemisphere) if isinstance(all_months, dict) else None
    if isinstance(months, str) and len(months) == 12 and set(months) <= {'0', '1'}:
        return months
    return None


def calendar_badges(metadata: dict[str, Any], hemisphere: str, selected_month: int) -> dict[str, bool]:
    """Compute all-year/new/leaving labels for a user-selected month (1-12)."""
    months = available_months(metadata, hemisphere)
    if months is None or selected_month not in range(1, 13):
        return {'year_round': False, 'new': False, 'leaving': False, 'seasonal': False}
    idx = selected_month - 1
    active = months[idx] == '1'
    return {
        'year_round': months == YEAR_ROUND,
        'seasonal': active and months != YEAR_ROUND,
        'new': active and months[(idx - 1) % 12] == '0',
        'leaving': active and months[(idx + 1) % 12] == '0',
    }


def _clock(hour: str, meridiem: str) -> int | None:
    n = int(hour)
    if n < 1 or n > 12:
        return None
    return (n % 12) + (12 if meridiem.upper() == 'PM' else 0)


def currently_catchable(metadata: dict[str, Any], hemisphere: str, now: datetime) -> bool:
    """Whether a critter is seasonally available and active at this instant.

    Handles both dash styles, multiple daily time windows, and overnight spans.
    Unknown/malformed time text is not advertised as available now.
    """
    months = available_months(metadata, hemisphere)
    if not months or months[now.month - 1] != '1':
        return False
    raw = metadata.get('time')
    if not isinstance(raw, str):
        return False
    if raw.strip().casefold() == 'all day':
        return True
    current_minute = now.hour * 60 + now.minute
    for start_hour, start_meridiem, end_hour, end_meridiem in TIME_SPAN.findall(raw):
        start = _clock(start_hour, start_meridiem)
        end = _clock(end_hour, end_meridiem)
        if start is None or end is None:
            continue
        begin, stop = start * 60, end * 60
        if begin == stop or (begin < stop and begin <= current_minute < stop):
            return True
        if begin > stop and (current_minute >= begin or current_minute < stop):
            return True
    return False


def display_metadata(category: str, original: dict[str, Any]) -> dict[str, Any]:
    """Prepare readable labels without mutating catalog records."""
    metadata = dict(original)
    if category == 'fish':
        value = metadata.get('size')
        if isinstance(value, str):
            metadata['size'] = FISH_SHADOW_NAMES.get(value, value)
    if category == 'fossil' and isinstance(metadata.get('type'), str):
        metadata['type'] = metadata['type'].replace('_', ' ').title()
    if category == 'diy':
        kind = metadata.get('type')
        if isinstance(kind, str):
            metadata['type'] = DIY_TYPE_NAMES.get(kind, kind)
        value = metadata.get('size')
        if isinstance(value, dict):
            width, height = value.get('width'), value.get('height')
            if isinstance(width, int) and isinstance(height, int):
                metadata['size'] = f'{width} × {height}'
            else:
                metadata.pop('size', None)
    return metadata

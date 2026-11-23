"""Catalog categories and validated packaged JSON entries.

Only the normalized catalog shipped in bundle/catalog.json is loaded at runtime.
The retired previous-application ZIP importers are intentionally not included.
"""
from __future__ import annotations

import json

CATEGORIES = {
    'fish': 'Fish', 'bugs': 'Bugs', 'deepsea': 'Sea Creatures',
    'fossil': 'Fossils', 'art': 'Art', 'diy': 'DIY Recipes',
}


def read_open_catalog(blob: bytes) -> list[tuple]:
    """Validate normalized bundled records, returning SQLite insert tuples."""
    if len(blob) > 5_000_000:
        raise ValueError('Catalog JSON too large')
    try:
        raw = json.loads(blob)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError('Invalid catalog JSON') from exc
    entries = raw.get('items') if isinstance(raw, dict) else None
    if not isinstance(entries, list) or len(entries) > 10000:
        raise ValueError('Expected an object with an items array')
    records = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError('Items must be objects')
        category, name = entry.get('category'), entry.get('name')
        if category not in CATEGORIES or not isinstance(name, str) or not 0 < len(name) <= 200:
            raise ValueError('Invalid category or item name')
        price, meta = entry.get('price'), entry.get('metadata', {})
        position = entry.get('position', index)
        if not isinstance(position, int) or isinstance(position, bool) or position < 0:
            raise ValueError('Invalid catalog position')
        if price is not None and (not isinstance(price, int) or isinstance(price, bool)):
            raise ValueError('Price must be an integer')
        if not isinstance(meta, dict):
            raise ValueError('Metadata must be an object')
        records.append((category, name, position, price, json.dumps(meta, ensure_ascii=False)))
    return records

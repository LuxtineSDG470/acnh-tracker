"""Install packaged ACNH collection data safely into persistent storage.

The catalog and artwork pack are release assets, not user data. Startup fills
missing records/images only; users' edits, checkmarks, and custom art win.
An artwork ZIP keeps image path casing intact when publishing via GitHub Desktop.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

from .catalog import read_open_catalog

BUNDLE_DIR = Path(__file__).resolve().parent.parent / 'bundle'
IMAGE_PACK = 'artwork-pack.zip'
MAX_IMAGES = 1_500
MAX_IMAGE_SIZE = 2_000_000
MAX_TOTAL_IMAGE_BYTES = 35_000_000


def _safe_relative_image(name: str) -> Path:
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        raise ValueError('Invalid bundled image name')
    path = PurePosixPath(name)
    if (path.is_absolute() or not path.parts or
            any(p in ('.', '..', '') for p in path.parts) or
            path.suffix not in ('.png', '.webp')):
        raise ValueError('Invalid bundled image path')
    return Path(*path.parts)


def inspect_bundled_data(bundle_dir: Path = BUNDLE_DIR):
    """Fail with a specific path on incomplete archives, before seeding.

    This check is run in GitHub Actions before the Docker image can publish.
    """
    manifest = bundle_dir / 'catalog.json'
    pack = bundle_dir / IMAGE_PACK
    if not manifest.is_file() or not pack.is_file():
        raise RuntimeError('Packaged catalog.json or artwork-pack.zip is missing')
    raw = manifest.read_bytes()
    records = read_open_catalog(raw)
    payload = json.loads(raw)
    mappings = payload.get('image_mappings')
    if not isinstance(mappings, list):
        raise ValueError('Packaged image map missing')
    valid_items = {(row[0], row[1]) for row in records}
    if len(records) != len(valid_items):
        raise ValueError('Duplicate items in packaged catalog')

    with zipfile.ZipFile(pack, 'r') as images:
        entries = images.infolist()
        if not entries or len(entries) > MAX_IMAGES:
            raise ValueError('Invalid number of images in artwork pack')
        paths = set()
        total = 0
        for entry in entries:
            if entry.is_dir():
                raise ValueError('Unexpected directory in artwork pack: ' + entry.filename)
            _safe_relative_image(entry.filename)
            if entry.filename in paths:
                raise ValueError('Duplicate artwork pack path: ' + entry.filename)
            paths.add(entry.filename)
            if not 0 < entry.file_size <= MAX_IMAGE_SIZE:
                raise ValueError('Invalid image size in pack: ' + entry.filename)
            total += entry.file_size
            if total > MAX_TOTAL_IMAGE_BYTES:
                raise ValueError('Artwork pack uncompressed size exceeds limit')
        # Validate actual bytes/CRC, not only ZIP directory entries.
        damaged = images.testzip()
        if damaged is not None:
            raise ValueError('Corrupt image in artwork pack: ' + damaged)

    prepared = []
    seen_mappings = set()
    for mapping in mappings:
        if not isinstance(mapping, list) or len(mapping) != 3:
            raise ValueError('Invalid packaged image map')
        cat, name, filename = mapping
        _safe_relative_image(filename)
        if (cat, name) not in valid_items:
            raise ValueError(f'Bundled image mapping points to a missing item: {cat}/{name}')
        if filename not in paths:
            raise ValueError(f'Bundled image is missing from artwork pack: {filename}')
        if (cat, name) in seen_mappings:
            raise ValueError(f'Duplicate bundled image mapping: {cat}/{name}')
        seen_mappings.add((cat, name))
        prepared.append((cat, name, filename))
    return records, prepared, paths


def install_bundled_data(db_path: str, data_dir: str, bundle_dir: Path = BUNDLE_DIR) -> dict[str, int]:
    """Seed missing artwork and catalog records without overwriting user content."""
    records, mappings, paths = inspect_bundled_data(bundle_dir)
    with closing(sqlite3.connect(db_path, timeout=15)) as con, con:
        before = con.execute('SELECT COUNT(*) FROM catalog_items').fetchone()[0]
        con.executemany('''INSERT OR IGNORE INTO catalog_items
            (category,name,position,price,metadata) VALUES(?,?,?,?,?)''', records)
        after = con.execute('SELECT COUNT(*) FROM catalog_items').fetchone()[0]

    target_root = Path(data_dir) / 'artwork'
    copied = 0
    with zipfile.ZipFile(bundle_dir / IMAGE_PACK, 'r') as images:
        for name in sorted(paths):
            relative = _safe_relative_image(name)
            target = target_root / relative
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.seed-', dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as output, images.open(name, 'r') as source:
                    shutil.copyfileobj(source, output)
                try:
                    os.link(temporary, target)  # atomically publish, no overwrite
                    copied += 1
                except FileExistsError:
                    pass
            finally:
                os.unlink(temporary)

    with closing(sqlite3.connect(db_path, timeout=15)) as con, con:
        con.executemany('''INSERT OR IGNORE INTO catalog_images
            (category,name,path) VALUES(?,?,?)''', mappings)

    return {'catalog_total': after, 'catalog_added': after - before,
            'images_copied': copied, 'images_mapped': len(mappings)}

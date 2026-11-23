#!/usr/bin/env python3
"""Fail early with the exact missing bundled image filename."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.seed import inspect_bundled_data

items, mappings, assets = inspect_bundled_data()
print(f'Artwork archive verified: {len(items)} items, {len(mappings)} mappings, {len(assets)} images')

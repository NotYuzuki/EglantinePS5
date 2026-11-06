# SPDX-License-Identifier: GPL-3.0-or-later
"""The folder that holds the driver checkouts (tools/radv-refs.sh has the rule and the reason).

EDEN_RADV_REFS when set, else the first line of .local/radv-refs (relative to the port), else the
port's parent folder. The tools that use it read this file by path, so they work under `python3 -I`.
"""
import os
from pathlib import Path


def radv_refs(root):
    root = Path(root)
    refs = os.environ.get('EDEN_RADV_REFS', '')
    marker = root / '.local/radv-refs'
    if not refs and marker.exists():
        lines = marker.read_text().splitlines()
        refs = lines[0].strip() if lines else ''
    if not refs:
        return root.parent
    path = Path(refs)
    return (path if path.is_absolute() else root / path).resolve()

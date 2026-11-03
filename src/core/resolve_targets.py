# -*- coding: utf-8 -*-
"""Набор файлов, которые имеет смысл резолвить отдельно.

Только LTX-корни: они дают итоговые конфиги. Include-файлы, которые
подключаются другими файлами, отдельно не резолвятся — их содержимое
уже входит в итоговый текст корня.
"""

from pathlib import Path


def resolved_ltx_roots(forest):
    """LTX-корни леса без XML и без дублей."""
    seen = set()
    out = []

    for path in (getattr(forest, "roots", ()) or ()):
        path = Path(path)
        if path.suffix.lower() != ".ltx":
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)

    return out

# -*- coding: utf-8 -*-
"""Модель DLTX-связей для дерева отображения.

Это **предсказание** того, какие ``mod_<root>_*.ltx`` будут автоматически
загружены движком. Оно нужно только для показа зависимостей в дереве до
запуска резолвера и не является частью загрузчика.

Модуль ничего не читает и не меняет в данных ``Resolver4``. Если правило
автозагрузки в резолвере изменится, это должно ловиться parity-тестом.
"""

from __future__ import annotations

from pathlib import Path

from core.tools.fs import pattern_match


def get_dltx_mods_for_root(root_path, folder_files):
    """Вернуть DLTX-патчи вида ``mod_<root>_*.ltx`` для корня.

    ``folder_files`` — уже собранный список файлов той же папки. Функция
    не сканирует диск и не меняет входные объекты.
    """
    stem = Path(root_path).stem.lower()
    folder_files = list(folder_files or [])

    ambiguous = {
        item.stem.lower()
        for item in folder_files
        if item.stem.lower().startswith(stem + "_")
    }

    mods = []
    prefix = "mod_" + stem + "_"

    for item in folder_files:
        name = item.name.lower()

        if not name.startswith(prefix):
            continue

        skip = False

        for amb in ambiguous:
            if amb == stem:
                continue

            head = "mod_" + amb + "_"

            if name.startswith(head) and pattern_match(name, head + "*.ltx"):
                skip = True
                break

        if not skip:
            mods.append(item)

    return sorted(mods, key=lambda item: item.name.lower())

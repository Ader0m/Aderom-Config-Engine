# -*- coding: utf-8 -*-
"""Настройки базового модуля.

Пути не абсолютные: кандидаты строятся относительно каталога запуска
(ярлыка приложения) и текущего каталога (VFS MO2).
"""

from pathlib import Path

# Относительно родителя корня ярлыка: для ACE.lnk это ../Anomaly 1.5.3 up.
UPSTREAM_GAME_DIR = Path("Anomaly 1.5.3 up") / "gamedata"
# Относительно текущего каталога: запуск из VFS MO2.
MO2_GAMEDATA_DIR = Path("gamedata")


def _app_root(app_dir: Path) -> Path:
    """Корень ярлыка/приложения: ``<root>`` для ``<root>/src``."""
    app_dir = Path(app_dir)
    if app_dir.name.lower() == "src":
        return app_dir.parent
    return app_dir


def config_dir_candidates(app_dir) -> list[Path]:
    """Каталоги, где может лежать игровая VFS с ``system.ltx``.

    ``detect_config_dir`` сам проверяет у каждого кандидата варианты
    ``<base>``, ``<base>/gamedata/configs`` и ``<base>/configs``.
    """
    app_dir = Path(app_dir).resolve()
    cwd = Path.cwd()
    app_root = _app_root(app_dir)

    candidates = [
        # VFS MO2: ./gamedata и текущий каталог.
        cwd / MO2_GAMEDATA_DIR,
        cwd,
        # Portable-установка: данные и игра рядом с приложением.
        app_root / MO2_GAMEDATA_DIR,
        app_root,
        # Ярлык запуска: ../Anomaly 1.5.3 up/gamedata.
        app_root.parent / UPSTREAM_GAME_DIR,
        app_root.parent / UPSTREAM_GAME_DIR.parent,
        # Каталоги кода/родителей — последний резерв.
        app_dir,
        app_dir.parent,
        app_dir.parent.parent,
    ]

    # Если игра лежит рядом, но папка называется иначе, находим её по
    # фактическому наличию gamedata/configs/system.ltx.
    for base in (cwd, app_root, app_root.parent):
        if base == base.parent:
            continue
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for child in entries:
            try:
                if (
                    child.is_dir()
                    and (child / "gamedata" / "configs" / "system.ltx").is_file()
                ):
                    candidates.append(child)
            except OSError:
                continue

    result = []
    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result

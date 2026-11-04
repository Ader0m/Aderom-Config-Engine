# -*- coding: utf-8 -*-
"""Настройки базового модуля."""

from pathlib import Path

# Старый хардкод оставлен только как последний fallback при автоопределении.
DEFAULT_CONFIG_DIR = r"../Anomaly 1.5.3 up/gamedata/configs"


def config_dir_candidates(app_dir) -> list[Path]:
    """Каталоги, где может лежать игровая VFS с ``system.ltx``."""
    app_dir = Path(app_dir)
    return [
        Path.cwd(),                                   # ./
        app_dir,                                      # src/
        app_dir.parent,                               # ../
        app_dir.parent.parent / "Anomaly 1.5.3 up",
        Path(DEFAULT_CONFIG_DIR),
    ]

# -*- coding: utf-8 -*-
"""Внешние инструменты. Пока оставлено для совместимости."""

import os
import shutil
import subprocess
from pathlib import Path


def open_in_notepad_pp(path):
    """
    Открывает файл в Notepad++.

    Возвращает:
        (True, "")
        (False, message)
    """
    cands = []

    exe = shutil.which("notepad++")
    if exe:
        cands.append(exe)

    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            cands.append(os.path.join(base, "Notepad++", "notepad++.exe"))

    for c in cands:
        try:
            if c and Path(c).is_file():
                subprocess.Popen([str(c), str(path)])
                return True, ""
        except Exception:
            pass

    return False, (
        "Notepad++ не найден (PATH, ProgramFiles, LocalAppData проверены)."
    )
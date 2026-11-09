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


def open_with_shell(path):
    """Открыть файл программой по умолчанию (Windows Shell).

    Для бинарных ресурсов (.dds, .ogf) ассоциация может отсутствовать.
    Тогда вместо исключения показываем системный диалог «Открыть с
    помощью…» через ``OpenAs_RunDLL``.

    Возвращает ``(True, "")`` или ``(False, message)``.
    """
    p = Path(path)
    try:
        if not p.is_file():
            return False, "Файл не найден: %s" % p
    except OSError as exc:
        return False, "Файл недоступен: %s (%s)" % (p, exc)

    startfile = getattr(os, "startfile", None)
    if startfile is not None:
        try:
            startfile(str(p))
            return True, ""
        except OSError:
            # Нет ассоциации или оболочка отказала — покажем выбор
            # приложения ниже.
            pass

    if os.name != "nt":
        return False, (
            "Открытие файла средствами оболочки поддерживается "
            "только в Windows."
        )

    try:
        subprocess.Popen([
            "rundll32.exe",
            "shell32.dll,OpenAs_RunDLL",
            str(p),
        ])
        return True, ""
    except Exception as exc:
        return False, "Не удалось открыть файл: %s" % exc

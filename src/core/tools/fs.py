# -*- coding: utf-8 -*-
"""Файловые операции, маски, поиск system.ltx и резолвера.

Include- и DLTX-модели для дерева живут в :mod:`core.tree.include_model` и
:mod:`core.tree.dltx_model`; здесь нет семантики загрузчика.
"""

import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Optional


RESOLVER_CANDIDATES = ("Resolver4.py", "resolver4.py")

# Текстовые файлы, которые имеет смысл читать при полном обходе VFS.
# Бинарные ресурсы (.dds, .ogf, .db и т.п.) сюда не попадают.
TEXT_FILE_SUFFIXES = frozenset({".ltx", ".xml", ".script", ".lua"})


# ------------------------------------------------------------ пути и маски

def normalize_path(p) -> Path:
    """
    Нормализация пути БЕЗ разыменования ссылок/junction.

    ВАЖНО: resolve() здесь запрещён. VFS MO2 может быть собрана
    ссылками/джанкшенами в каталоге игры; resolve() подменит игровой путь
    на реальный путь мода (MO2\mods\...), и мы потеряем вид "как в игре".
    """
    return Path(os.path.normpath(os.path.abspath(str(p))))


def detect_config_dir(candidates):
    """
    Ищет каталог конфигов игровой VFS среди кандидатов.
    Проверяет: <base>/system.ltx, <base>/gamedata/configs/system.ltx,
    <base>/configs/system.ltx.
    """
    for c in candidates:
        base = Path(c)
        for sub in (Path(), Path("gamedata") / "configs", Path("configs")):
            d = base / sub
            try:
                if (d / "system.ltx").is_file():
                    return normalize_path(d)
            except OSError:
                continue
    return None


def canonical_config_dir(config_dir):
    """Привести выбранный каталог к реальному каталогу configs.

    Пользователь/UI может передать корень игры, ``gamedata`` или уже
    ``gamedata/configs``. XML-резолву и ключу кэша нужен именно каталог
    с ``system.ltx``, иначе include-пути и ключи будут разными.
    """
    if config_dir is None:
        return None
    found = detect_config_dir([config_dir])
    if found is not None:
        return found
    return normalize_path(config_dir)


def rel_path(p, base) -> str:
    try:
        return os.path.relpath(str(p), str(base))
    except ValueError:
        return str(p)


def pattern_match(name: str, mask: str) -> bool:
    """
    FS-маска '*'/'?' как в PatternMatch из LocatorAPI.cpp.
    Регистронезависимо.
    """
    rx = "^" + "".join(
        ".*" if c == "*" else "." if c == "?" else re.escape(c)
        for c in str(mask).lower()
    ) + "$"

    return re.match(rx, str(name).lower()) is not None


def read_text_safe(path) -> str:
    """
    Читает текст с поддержкой UTF-8 BOM и fallback на windows-1251.
    """
    raw = Path(path).read_bytes()

    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("windows-1251", errors="replace")


def read_xml_text(path) -> str:
    """Читает XML строго как windows-1251 (без автоопределения).

    Конфиги X-Ray/Anomaly объявляют ``encoding="windows-1251"``.
    BOM, если он есть, отбрасывается; остальные байты декодируются 1:1.
    """
    raw = Path(path).read_bytes()

    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]

    return raw.decode("windows-1251", errors="replace")


# ------------------------------------------------------------ include

def strip_comments(line: str) -> str:
    """
    Убирает комментарии:
    - ';'
    - '//'

    Комментарий внутри кавычек не учитывается.
    """
    # Быстрый путь: в большинстве строк конфигов комментариев нет.
    # Проверка подстроки выполняется в C и не требует посимвольного цикла.
    if ";" not in line and "//" not in line:
        return line.strip()

    in_q = False
    i = 0
    n = len(line)

    while i < n:
        ch = line[i]

        if ch == '"':
            in_q = not in_q
        elif not in_q:
            if ch == ";":
                return line[:i].strip()

            if ch == "/" and i + 1 < n and line[i + 1] == "/":
                return line[:i].strip()

        i += 1

    return line.strip()


# ------------------------------------------------------------ сканирование

def scan_files(root_dir, suffixes=None):
    """
    Рекурсивно находит файлы под root_dir.

    ``suffixes`` — необязательный набор расширений (например,
    ``TEXT_FILE_SUFFIXES``). Если не задан, возвращаются все файлы.
    """
    out = set()

    try:
        root = Path(root_dir)

        if root.is_file():
            root = root.parent

        allowed = None
        if suffixes is not None:
            allowed = {str(s).lower() for s in suffixes}

        for p in root.rglob("*"):
            try:
                if not p.is_file():
                    continue
                if allowed is not None and p.suffix.lower() not in allowed:
                    continue
                out.add(normalize_path(p))
            except OSError:
                pass
    except Exception:
        pass

    return sorted(out, key=lambda x: str(x).lower())


def scan_ltx_files(root_dir):
    """
    Рекурсивно находит все .ltx файлы.
    """
    return scan_files(root_dir, {".ltx"})


def scan_xml_files(root_dir):
    """
    Рекурсивно находит все .xml файлы.

    XML — отдельный, независимый от LTX набор: у него нет #include и он
    не участвует в проходе резолвера. Ядро лишь показывает его в дереве,
    чтобы файл можно было открыть сырым текстом.
    """
    return scan_files(root_dir, {".xml"})


def find_system_ltx(selected_dir) -> Optional[Path]:
    """
    Ищет system.ltx в типичных местах.
    """
    if not selected_dir:
        return None

    sel = Path(selected_dir)

    if sel.is_file():
        sel = sel.parent

    if not sel.is_dir():
        return None

    candidates = (
        sel / "system.ltx",
        sel / "gamedata" / "configs" / "system.ltx",
        sel / "configs" / "system.ltx",
    )

    for c in candidates:
        if c.is_file():
            return normalize_path(c)

    for p in sel.rglob("system.ltx"):
        if p.is_file():
            return normalize_path(p)

    return None


# ------------------------------------------------------------ резолвер

def build_search_bases(scan_dir, selected_dir=None):
    """
    Список баз, где может лежать Resolver4.py.
    """
    bases = []

    for d in (
        scan_dir,
        selected_dir,
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parent.parent,
        Path.cwd(),
    ):
        if not d:
            continue

        try:
            p = Path(d).expanduser()

            if p.is_file():
                p = p.parent

            p = p.resolve()

            if p not in bases:
                bases.append(p)

            if p.parent not in bases:
                bases.append(p.parent)
        except Exception:
            continue

    return bases


def find_resolver_script(bases=None) -> Optional[Path]:
    """
    Ищет Resolver4.py.

    Приоритет:
    1. Папка ``core/resolver/``.
    2. Папка ``core/`` (совместимость со старым расположением).
    3. Переданные базы.
    4. Импорт через importlib.
    """
    tools_dir = Path(__file__).resolve().parent
    core_dir = tools_dir.parent
    resolver_dir = core_dir / "resolver"

    for directory in (resolver_dir, core_dir):
        for name in RESOLVER_CANDIDATES:
            p = directory / name
            if p.is_file():
                return normalize_path(p)

    if bases is None:
        bases = build_search_bases(core_dir.parent, core_dir)

    for base in bases:
        for name in RESOLVER_CANDIDATES:
            p = Path(base) / name
            if p.is_file():
                return normalize_path(p)

    for name in RESOLVER_CANDIDATES:
        try:
            spec = importlib.util.find_spec(Path(name).stem)
            if spec and spec.origin and Path(spec.origin).is_file():
                return normalize_path(spec.origin)
        except Exception:
            pass

    return None


def load_resolver_module(path):
    """
    Загружает Resolver4.py как модуль.
    """
    p = Path(path)

    if not p.is_file():
        raise FileNotFoundError("Resolver не найден: %s" % path)

    spec = importlib.util.spec_from_file_location(p.stem, str(p))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[p.stem] = mod
    spec.loader.exec_module(mod)

    return mod


# ------------------------------------------------------------ резолвер
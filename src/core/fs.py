# -*- coding: utf-8 -*-
"""Файловые операции, маски, include, поиск system.ltx и резолвера."""

import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Optional


RESOLVER_CANDIDATES = ("Resolver4.py", "resolver4.py")


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


# ------------------------------------------------------------ include

def strip_comments(line: str) -> str:
    """
    Убирает комментарии:
    - ';'
    - '//'

    Комментарий внутри кавычек не учитывается.
    """
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


INCLUDE_QUOTED_RE = re.compile(r'#include\s+"([^"]+)"', re.IGNORECASE)
INCLUDE_BARE_RE = re.compile(r'#include\s+([^\s;]+)', re.IGNORECASE)


def extract_includes(text: str):
    """
    Возвращает список include-директив из текста LTX.
    """
    out = []

    for line in text.splitlines():
        s = strip_comments(line)

        if not s:
            continue

        if not s.startswith("#"):
            continue

        if "include" not in s.lower():
            continue

        m = INCLUDE_QUOTED_RE.search(s)
        if m:
            out.append(m.group(1))
            continue

        m = INCLUDE_BARE_RE.search(s)
        if m:
            out.append(m.group(1).strip('"'))

    return out


def resolve_include_targets(inc_name: str, parent_path):
    """
    Разрешение #include относительно файла-родителя.
    Поддерживаются маски '*' и '?'.
    """
    inc = str(inc_name or "").strip().strip('"').replace("\\", "/")

    if not inc:
        return []

    parent_dir = Path(parent_path).parent
    p = Path(inc)

    if p.is_absolute():
        if "*" in inc or "?" in inc:
            d = p.parent
            mask = p.name

            if not d.is_dir():
                return []

            return sorted(
                (
                    normalize_path(e)
                    for e in d.iterdir()
                    if e.is_file()
                    and e.suffix.lower() == ".ltx"
                    and pattern_match(e.name, mask)
                ),
                key=lambda x: str(x).lower(),
            )

        f = normalize_path(p)
        return [f] if f.is_file() else []

    inc_rel = inc.lstrip("/")

    if "*" in inc_rel or "?" in inc_rel:
        parts = inc_rel.split("/")
        mask = parts[-1]
        subdir = "/".join(parts[:-1])

        d = parent_dir / subdir if subdir else parent_dir

        if not d.is_dir():
            return []

        return sorted(
            (
                normalize_path(e)
                for e in d.iterdir()
                if e.is_file()
                and e.suffix.lower() == ".ltx"
                and pattern_match(e.name, mask)
            ),
            key=lambda x: str(x).lower(),
        )

    f = normalize_path(parent_dir / inc_rel)
    if f.is_file():
        return [f]

    f2 = normalize_path(parent_dir / inc)
    return [f2] if f2.is_file() else []


# ------------------------------------------------------------ сканирование

def scan_ltx_files(root_dir):
    """
    Рекурсивно находит все .ltx файлы.
    """
    out = set()

    try:
        root = Path(root_dir)

        if root.is_file():
            root = root.parent

        for p in root.rglob("*"):
            try:
                if p.is_file() and p.suffix.lower() == ".ltx":
                    out.add(normalize_path(p))
            except OSError:
                pass
    except Exception:
        pass

    return sorted(out, key=lambda x: str(x).lower())


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
    1. Папка core/, где лежит этот файл.
    2. Переданные базы.
    3. Импорт через importlib.
    """
    core_dir = Path(__file__).resolve().parent

    for name in RESOLVER_CANDIDATES:
        p = core_dir / name
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


# ------------------------------------------------------------ DLTX mod-файлы

def get_dltx_mods_for_root(root_path, folder_files):
    """
    Возвращает DLTX-патчи вида:

        mod_<имя_базового_файла>_<что-то>.ltx

    для конкретного корневого файла.
    """
    stem = Path(root_path).stem.lower()
    folder_files = list(folder_files or [])

    ambiguous = {
        f.stem.lower()
        for f in folder_files
        if f.stem.lower().startswith(stem + "_")
    }

    mods = []
    prefix = "mod_" + stem + "_"

    for f in folder_files:
        name = f.name.lower()

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
            mods.append(f)

    return sorted(mods, key=lambda x: x.name.lower())


# ------------------------------------------------------------ source map

def build_source_map(forest, field="real_paths") -> dict:
    """
    Карта источников для резолвера.

    Ключи — нижнерегистровые stem'ы файлов (как ожидает pretty_source).
    Значения — человекочитаемая подпись из запрошенной таблицы леса:
        field="real_paths" (по умолчанию) — физическое расположение (MO2/GAME);
        field="rel_paths"                 — игровой VFS-вид.
    """
    table = getattr(forest, field, None) or {}
    mp = {}

    for f in forest.files:
        disp = table.get(f) or forest.rel_paths.get(f, str(f))

        key = os.path.splitext(str(f).lower())[0]
        mp[key] = disp

        base_key = os.path.splitext(f.name.lower())[0]
        if base_key not in mp:
            mp[base_key] = disp

    return mp


def pretty_source(source, source_map) -> str:
    """
    Возвращает человекочитаемый источник по данным из source_map.
    """
    if not source:
        return "?"

    s = str(source).lower()

    if s in source_map:
        return source_map[s]

    s2 = os.path.splitext(s)[0]
    if s2 in source_map:
        return source_map[s2]

    b = os.path.basename(s2)
    return source_map.get(b, source)

def describe_real_path(real: Path, game_root_real: Path) -> str:
    """
    Человекочитаемое описание фактического расположения файла:

        MO2\<имя мода>\<путь внутри мода>   — если файл лежит в моде MO2;
        GAME\<путь от configs игры>        — если файл оригинальный (в игре);
        абсолютный путь                    — во всех остальных случаях.
    """
    parts = real.parts
    low = [x.lower() for x in parts]

    if "mods" in low:
        idx = low.index("mods")
        if idx + 1 < len(parts):
            mod = parts[idx + 1]
            rel = parts[idx + 2:]
            if rel:
                return "MO2\\%s\\%s" % (mod, "\\".join(rel))
            return "MO2\\%s" % mod

    try:
        rel = os.path.relpath(str(real), str(game_root_real))
        if not rel.startswith(".."):
            return "GAME\\%s" % rel
    except ValueError:
        pass

    return str(real)
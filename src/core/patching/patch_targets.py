# -*- coding: utf-8 -*-
"""Определение цели патча и безопасных путей патч-мода.

Главное правило: патч привязывается к **корню загрузки**, а не к
непосредственному include-родителю. Корень загрузки — файл, который
движок открывает как самостоятельный конфиг и для которого ищет
``mod_<имя>_*.ltx``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

from ..fs import normalize_path
from .patch_models import PatchTarget


_SAFE_NAME_RE = re.compile(r"[^0-9a-zA-Zа-яА-ЯёЁ_.~-]+")
_DRIVE_RE = re.compile(r"^[a-zA-Z]:")


def _as_path(value) -> Path:
    return normalize_path(Path(value))


def forest_scan_dir(forest) -> Optional[Path]:
    scan_dir = getattr(forest, "scan_dir", None)
    if not scan_dir:
        return None
    return _as_path(scan_dir)


def _forest_files(forest) -> Tuple[Path, ...]:
    files = getattr(forest, "files", None)
    if files is None:
        files = [
            Path(p)
            for p in (getattr(forest, "rel_paths", {}) or {})
            if Path(p).suffix.lower() == ".ltx"
        ]
    return tuple(_as_path(p) for p in files)


def _parents_map(forest) -> dict:
    result = {}
    for path, parents in (getattr(forest, "include_parents", {}) or {}).items():
        result[_as_path(path)] = tuple(_as_path(p) for p in parents)
    return result


def root_candidates(forest, source_path) -> Tuple[Path, ...]:
    """Вернуть все корни загрузки, к которым относится файл."""
    source = _as_path(source_path)
    parents = _parents_map(forest)
    files = set(_forest_files(forest))

    if source not in files and source not in parents:
        return (source,)

    def walk(node: Path, trail: frozenset) -> set:
        if node in trail:
            return {node}

        node_parents = parents.get(node, ())

        if not node_parents:
            return {node}

        roots = set()
        for parent in node_parents:
            if parent.name.lower().startswith("mod_"):
                # Непривязанный mod_*.ltx не является корнем загрузки,
                # но и настоящего корня у него нет.
                roots.add(parent)
                continue
            roots.update(walk(parent, trail | {node}))
        return roots

    found = walk(source, frozenset())
    return tuple(sorted(found, key=lambda p: str(p).lower()))


def resolve_patch_target(
    forest,
    source_path,
    explicit_root=None,
) -> PatchTarget:
    """Определить корень загрузки для изменения файла."""
    source = _as_path(source_path)
    candidates = root_candidates(forest, source)

    if explicit_root is not None:
        root = _as_path(explicit_root)
        return PatchTarget(
            source_path=source,
            root_path=root,
            candidates=candidates,
            reason="Корень задан явно",
            ambiguous=False,
        )

    if len(candidates) == 1:
        return PatchTarget(
            source_path=source,
            root_path=candidates[0],
            candidates=candidates,
            reason="Единственный корень загрузки",
            ambiguous=False,
        )

    if not candidates:
        return PatchTarget(
            source_path=source,
            root_path=None,
            candidates=(),
            reason="Корень загрузки не найден",
            ambiguous=True,
        )

    return PatchTarget(
        source_path=source,
        root_path=None,
        candidates=candidates,
        reason=(
            "Файл входит в несколько корней загрузки; нужен явный выбор"
        ),
        ambiguous=True,
    )


def sanitize_name(value: str, fallback: str = "patch") -> str:
    value = (value or "").strip().lower()
    value = _SAFE_NAME_RE.sub("_", value)
    value = value.strip("._-")
    return value or fallback


def _load_last_tail(suffix: str) -> str:
    """Суффикс, который сортируется после обычных имён модов.

    Resolver4 загружает ``mod_<root>_*.ltx`` по возрастанию имени, поэтому
    патч должен стоять в конце списка. Префикс ``~`` (0x7E) старше
    латинских букв, цифр и точки с запятой, которые обычно встречаются
    в именах модов.
    """
    tail = sanitize_name(suffix, "ace")
    if not tail.startswith("~"):
        tail = "~" + tail
    return tail


def patch_file_name(root_path, suffix: str = "ace") -> str:
    """Имя DLTX-файла для корня: ``mod_<root>_~<suffix>.ltx``."""
    root = Path(root_path)
    stem = sanitize_name(root.stem, "root")
    tail = _load_last_tail(suffix)
    return "mod_%s_%s.ltx" % (stem, tail)


def _mod_prefix(root_path) -> str:
    return "mod_%s_" % sanitize_name(Path(root_path).stem, "root")


def _existing_suffixes(root_path, existing_names) -> list:
    prefix = _mod_prefix(root_path)
    result = []

    for name in existing_names or ():
        low = str(name).lower()
        if not low.startswith(prefix) or not low.endswith(".ltx"):
            continue
        suffix = low[len(prefix):-4]
        if suffix:
            result.append(suffix)

    return result


def choose_load_last_suffix(
    root_path,
    existing_names=(),
    preferred: str = "ace",
) -> str:
    """Выбрать суффикс, который загрузится после существующих патчей."""
    base = _load_last_tail(preferred)
    suffixes = _existing_suffixes(root_path, existing_names)

    if not suffixes:
        return base

    maximum = max(suffixes)

    if base > maximum and base not in suffixes:
        return base

    candidate = maximum + "~"
    while candidate in suffixes:
        candidate += "~"
    return candidate


def patch_file_rel(forest, root_path, suffix: str = "ace") -> Path:
    """Путь патч-файла относительно ``gamedata/configs``."""
    root = _as_path(root_path)
    name = patch_file_name(root, suffix)
    scan_dir = forest_scan_dir(forest)

    if scan_dir is not None:
        try:
            rel_parent = root.parent.relative_to(scan_dir)
        except ValueError:
            rel_parent = Path()
    else:
        rel_parent = Path()

    return rel_parent / name


def patch_mod_file_rel(forest, root_path, suffix: str = "ace") -> Path:
    """Путь файла внутри патч-мода, включая ``gamedata/configs``."""
    return Path("gamedata") / "configs" / patch_file_rel(
        forest,
        root_path,
        suffix,
    )


def suggest_patch_mod_name(root_path) -> str:
    root = Path(root_path)
    return "ACE Patch - %s" % root.stem


def unique_suffix(root_path, existing_names: Iterable[str] = ()) -> str:
    """Суффикс, который не совпадает с существующими патчами и идёт после них."""
    return choose_load_last_suffix(root_path, existing_names, "ace")


def is_under(path: Path, parent: Path) -> bool:
    """True, если ``path`` лежит внутри ``parent``."""
    try:
        path = _as_path(path)
        parent = _as_path(parent)
        return os.path.commonpath([str(path), str(parent)]) == str(parent)
    except (ValueError, OSError):
        return False


def validate_patch_mod_root(
    patch_mod_root,
    forbidden_roots: Sequence[Path] = (),
) -> Tuple[bool, str]:
    """Проверить, что каталог патч-мода не является оригиналом/Overwrite."""
    root = _as_path(patch_mod_root)

    if not str(root).strip():
        return False, "Не задан каталог патч-мода"

    parts = [part.lower() for part in root.parts]
    if "overwrite" in parts:
        return False, "Каталог Overwrite запрещён для патчей"

    for forbidden in forbidden_roots:
        if forbidden is None:
            continue
        if is_under(root, forbidden):
            return (
                False,
                "Каталог патча находится внутри защищённого каталога: %s"
                % forbidden,
            )

    if _DRIVE_RE.match(str(root)):
        # Абсолютный путь допустим: это отдельный мод, а не VFS.
        return True, ""

    if root.is_absolute():
        return True, ""

    return False, "Каталог патч-мода должен быть абсолютным"

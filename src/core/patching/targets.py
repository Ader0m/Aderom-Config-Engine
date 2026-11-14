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

from core.tools.fs import normalize_path
from core.patching.models import PatchTarget


_SAFE_NAME_RE = re.compile(r"[^0-9a-zA-Zа-яА-ЯёЁ_.~-]+")
_DRIVE_RE = re.compile(r"^[a-zA-Z]:")


def _as_path(value) -> Path:
    return normalize_path(Path(value))


def forest_scan_dir(forest) -> Optional[Path]:
    scan_dir = getattr(forest, "scan_dir", None)
    if not scan_dir:
        return None
    return _as_path(scan_dir)


_INDEX_ATTR = "_ace_patch_target_index"
_XML_INDEX_ATTR = "_ace_xml_patch_target_index"

_DERIVED_FOREST_ATTRS = (
    _INDEX_ATTR,
    _XML_INDEX_ATTR,
    "_resolved_signature_memo",
)


def invalidate_forest_indexes(forest) -> None:
    """Сбросить производные индексы, приклеенные к лесу.

    Лес — разделяемый объект: после добавления/удаления виртуального
    файла его ``files``/``include_parents``/``xml_files`` меняются, и
    старый индекс целей патча уже не соответствует содержимому.
    """
    for attr in _DERIVED_FOREST_ATTRS:
        try:
            delattr(forest, attr)
        except (AttributeError, TypeError):
            pass


def _forest_index(forest):
    """Нормализованные файлы и карта родителей, посчитанные один раз.

    ``root_candidates`` вызывается для каждого изменённого файла проекта.
    При этом раньше на каждый вызов заново нормализовались все пути дерева
    (для больших VFS — десятки тысяч). Индекс хранится на самом forest и
    живёт столько же, сколько дерево.
    """
    cached = getattr(forest, _INDEX_ATTR, None)
    if cached is not None:
        return cached

    files = getattr(forest, "files", None)
    if files is None:
        files = [
            Path(p)
            for p in (getattr(forest, "rel_paths", {}) or {})
            if Path(p).suffix.lower() == ".ltx"
        ]

    normalized_files = frozenset(_as_path(p) for p in files)

    parents = {}
    for path, values in (
        getattr(forest, "include_parents", {}) or {}
    ).items():
        parents[_as_path(path)] = tuple(_as_path(p) for p in values)

    index = (normalized_files, parents)

    try:
        setattr(forest, _INDEX_ATTR, index)
    except Exception:
        # Forest-подобные объекты из тестов могут запрещать атрибуты.
        pass

    return index


def _root_from_patch_name(path, files) -> Optional[Path]:
    """Найти корень по имени DLTX-патча ``mod_<root>_<suffix>.ltx``.

    Нужен для случаев, когда патч ещё не привязан к корню в дереве
    (например, forest из теста или только что созданный виртуальный
    файл). Ищем самый длинный подходящий stem в той же папке.
    """
    path = _as_path(path)
    name = path.stem.lower()
    if not name.startswith("mod_"):
        return None

    remainder = name[4:]
    best = None

    for candidate in files:
        candidate = _as_path(candidate)
        if candidate.parent != path.parent:
            continue
        if candidate.name.lower().startswith("mod_"):
            continue

        stem = candidate.stem.lower()
        if not remainder.startswith(stem + "_"):
            continue

        if best is None or len(stem) > len(best.stem):
            best = candidate

    return best


def root_candidates(forest, source_path) -> Tuple[Path, ...]:
    """Вернуть все корни загрузки, к которым относится файл."""
    source = _as_path(source_path)
    files, parents = _forest_index(forest)

    if source not in files and source not in parents:
        return (source,)

    if not parents.get(source) and source.name.lower().startswith("mod_"):
        base = _root_from_patch_name(source, files)
        if base is not None and base != source:
            return root_candidates(forest, base)

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


# Кэш списка mod_*.ltx в каталоге корня. Планирование вызывается для
# каждого изменённого файла, а каталог gamedata/configs может содержать
# тысячи файлов; повторный listdir на каждый план не нужен.
_PATCH_NAMES_CACHE = {}


def existing_patch_names(root_path) -> list:
    """Имена ``mod_*`` в каталоге корня с кэшем по mtime каталога."""
    parent = Path(root_path).parent
    key = str(parent).lower()

    try:
        mtime = parent.stat().st_mtime_ns
    except OSError:
        return []

    cached = _PATCH_NAMES_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return list(cached[1])

    try:
        names = [
            entry.name
            for entry in parent.iterdir()
            if entry.is_file() and entry.name.lower().startswith("mod_")
        ]
    except OSError:
        return []

    _PATCH_NAMES_CACHE[key] = (mtime, tuple(names))
    return names


def preferred_suffix(options) -> str:
    """Суффикс патча с учётом проекта: ``ace`` или ``ace_<project>``."""
    suffix = getattr(options, "suffix", "") or "ace"
    project_tag = getattr(options, "project_tag", "") or ""
    if project_tag:
        suffix = "%s_%s" % (suffix, project_tag)
    return suffix


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


def validate_patch_files(
    generated,
    allowed_configs=(".ltx", ".xml"),
    allow_scripts: bool = False,
    asset_rules=None,
) -> Tuple[bool, str]:
    """Проверить относительные пути файлов сгенерированного патча.

    ``allowed_configs`` — разрешённые расширения в ``gamedata/configs``;
    ``None`` означает «любое расширение». ``allow_scripts`` разрешает
    DXML-скрипт строго в ``gamedata/scripts/modxml_*.script``.
    ``asset_rules`` — ``{каталог: (расширения, ...)}`` для двоичных
    ресурсов, зарегистрированных модулями (например,
    ``{"sounds": (".ogg",)}``).
    """
    rules = {}
    for root, suffixes in (asset_rules or {}).items():
        normalized = {
            str(suffix).lower()
            for suffix in (suffixes or ())
            if str(suffix)
        }
        if normalized:
            rules[str(root).lower()] = normalized

    for file in getattr(generated, "files", ()) or ():
        rel = Path(file.relative_path)

        if rel.is_absolute() or ".." in rel.parts:
            return False, "Небезопасный путь патч-файла: %s" % rel

        parts = [part.lower() for part in rel.parts]
        if "overwrite" in parts:
            return False, "Патч не должен попадать в Overwrite"

        if tuple(parts[:2]) == ("gamedata", "configs"):
            if (
                allowed_configs is not None
                and rel.suffix.lower() not in allowed_configs
            ):
                return False, (
                    "В gamedata/configs недопустимое расширение: %s" % rel
                )
            continue

        if len(parts) >= 2 and parts[0] == "gamedata":
            root = parts[1]
            if root in rules:
                if (
                    rel.suffix.lower() in rules[root]
                    and len(parts) >= 3
                ):
                    continue
                return False, (
                    "В gamedata/%s недопустимый ресурс: %s"
                    % (root, rel)
                )

        if allow_scripts and tuple(parts[:2]) == ("gamedata", "scripts"):
            if rel.suffix.lower() != ".script":
                return False, (
                    "DXML-патч должен иметь расширение .script: %s" % rel
                )
            if len(parts) != 3 or not parts[2].startswith("modxml_"):
                return False, (
                    "DXML-скрипт должен лежать в gamedata/scripts и "
                    "начинаться с modxml_: %s" % rel
                )
            continue

        if allow_scripts:
            return False, (
                "Патч должен лежать в gamedata/configs или "
                "gamedata/scripts: %s" % rel
            )
        return False, "Патч должен лежать в gamedata/configs: %s" % rel

    return True, ""


# ---------------------------------------------------------------- XML


_XML_INCLUDE_RE = re.compile(r'"([^"]*)"')


def xml_relative_path(forest, source_path) -> Path:
    """Путь XML-файла относительно ``gamedata/configs``."""
    source = _as_path(source_path)
    rel_paths = getattr(forest, "rel_paths", None) or {}
    value = rel_paths.get(source)
    if value is None:
        value = rel_paths.get(str(source))
    if value is not None:
        return Path(str(value).replace("/", "\\"))
    for path, candidate in rel_paths.items():
        if _as_path(path) == source:
            return Path(str(candidate).replace("/", "\\"))
    scan_dir = forest_scan_dir(forest)
    if scan_dir is not None:
        try:
            return source.relative_to(scan_dir)
        except ValueError:
            pass
    return Path(source.name)


def xml_virtual_name(config_dir, path) -> str:
    """Игровое имя XML-файла: ``ui\\file.xml``."""
    path = _as_path(path)
    config_dir = _as_path(config_dir) if config_dir is not None else None
    if config_dir is not None:
        try:
            return str(path.relative_to(config_dir)).replace("/", "\\")
        except ValueError:
            pass
    return str(path.name)


def _xml_include_targets(
    include: str,
    config_dir: Path,
    files: Sequence[Path],
    by_norm=None,
    virtual_names=None,
):
    from core.tools.fs import pattern_match

    include = str(include).replace("/", "\\").strip()
    if not include:
        return []

    if "*" in include or "?" in include:
        result = []
        names = virtual_names or {
            path: xml_virtual_name(config_dir, path) for path in files
        }
        for path, rel in names.items():
            if pattern_match(rel, include):
                result.append(path)
        return result

    target = config_dir / include.replace("\\", "/")
    if by_norm is not None:
        found = by_norm.get(
            os.path.normcase(os.path.abspath(str(target)))
        )
        return [found] if found is not None else []

    for path in files:
        if _as_path(path) == _as_path(target):
            return [path]
    return []


def _xml_target_index(forest):
    cached = getattr(forest, _XML_INDEX_ATTR, None)
    if cached is not None:
        return cached

    from core.tools.fs import read_xml_text

    files = [
        _as_path(path)
        for path in (getattr(forest, "xml_files", None) or ())
    ]
    config_dir = forest_scan_dir(forest)
    parents = {}

    if config_dir is not None:
        by_norm = {
            os.path.normcase(os.path.abspath(str(path))): path
            for path in files
        }
        virtual_names = {
            path: xml_virtual_name(config_dir, path)
            for path in files
        }
        for path in files:
            try:
                text = read_xml_text(path)
            except Exception:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if not (stripped.startswith("#") and "#include" in stripped):
                    continue
                match = _XML_INCLUDE_RE.search(stripped)
                if not match:
                    continue
                for child in _xml_include_targets(
                    match.group(1),
                    config_dir,
                    files,
                    by_norm=by_norm,
                    virtual_names=virtual_names,
                ):
                    parents.setdefault(child, set()).add(path)

    index = (frozenset(files), config_dir, parents)
    try:
        setattr(forest, _XML_INDEX_ATTR, index)
    except Exception:
        pass
    return index


def xml_root_candidates(forest, source_path) -> Tuple[Path, ...]:
    """Все XML-корни, в include-дерево которых входит файл."""
    source = _as_path(source_path)
    files, _config_parts, parents = _xml_target_index(forest)

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
            roots.update(walk(parent, trail | {node}))
        return roots

    found = walk(source, frozenset())
    return tuple(sorted(found, key=lambda p: str(p).lower()))


def xml_root_virtual_names(config_dir, roots) -> Tuple[str, ...]:
    return tuple(
        xml_virtual_name(config_dir, root)
        for root in roots
    )


def xml_patch_file_rel(forest, suffix: str = "ace") -> Path:
    """Имя DXML-скрипта в ``gamedata/scripts``."""
    tail = sanitize_name(suffix, "ace")
    if not tail.startswith("~"):
        tail = "~" + tail
    return Path("modxml_%s.script" % tail)

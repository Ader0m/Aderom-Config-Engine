# -*- coding: utf-8 -*-
"""Форк XML-ключа и перепривязка ссылок.

Режим «XML (копия или новые файлы)» не изменяет существующий XML на месте.
Он делает следующее:

1. находит изменённый элемент с ``id``/``name``;
2. создаёт его копию с новым ключом в новом XML-файле;
3. ищет ссылки на старый ключ во всех LTX/XML VFS;
4. для LTX-ссылок строит DLTX-патч, для XML-ссылок — DXML-патч;
5. ссылки в ``gamedata/scripts`` не патчит, а показывает предупреждением.

Новый XML-файл и патчи ссылок собираются в один ``GeneratedPatch``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from core.patching.ltx.document import parse_ltx_document
from core.patching.ltx.source import replace_values
from core.patching.xml.diff import (
    XmlChange,
    XmlDocumentDiff,
    apply_xml_changes,
    diff_xml_documents,
)
from core.patching.xml.document import (
    XmlDocument,
    XmlModelError,
    XmlNode,
    parse_xml_document,
    serialize_node,
    serialize_xml_document,
)
from core.patching.xml.generator import DxmlGenerator
from core.patching.ltx.merge import merge_patch_plans
from core.patching.models import (
    GeneratedPatch,
    PatchIssue,
    PatchOptions,
    PatchPlan,
    PatchTarget,
    VerificationCheck,
    VerificationResult,
)
from core.patching.targets import (
    sanitize_name,
    xml_patch_file_rel,
    xml_relative_path,
    xml_root_candidates,
    xml_root_virtual_names,
)


class XmlForkError(Exception):
    """XML-ключ нельзя форкнуть."""


@dataclass(frozen=True)
class XmlForkKey:
    """Один форкнутый XML-элемент."""

    old_key: Optional[str]
    new_key: str
    key_attr: str
    source_path: Path
    before_path: Tuple[Tuple[str, int], ...]
    node_xml: str


@dataclass(frozen=True)
class XmlForkReference:
    """Ссылка на старый ключ."""

    kind: str                  # ltx | xml | script
    path: Path
    section: Optional[str] = None
    key: Optional[str] = None
    old_value: str = ""
    new_value: str = ""
    line: int = 0
    excerpt: str = ""


@dataclass(frozen=True)
class XmlForkResult:
    """Результат форка и перепривязки."""

    plan: PatchPlan
    generated: GeneratedPatch
    keys: Tuple[XmlForkKey, ...] = ()
    references: Tuple[XmlForkReference, ...] = ()
    # DLTX-планы перепривязки LTX-ссылок. Уровень проекта объединяет их
    # с остальными планами того же корня, чтобы не создать два патч-файла
    # с одним именем.
    ltx_plans: Tuple[PatchPlan, ...] = ()


# ---------------------------------------------------------------- helpers


def _new_file_name(anchor: Path, project_tag: str) -> str:
    tag = sanitize_name(project_tag or "ace", "ace")
    return "%s_ace_%s.xml" % (sanitize_name(Path(anchor).stem, "xml"), tag)


def _locale_of(path, config_dir=None) -> Optional[str]:
    """Вернуть язык для ``text\\<lang>\\...``."""
    try:
        path = Path(path)
        if config_dir is not None:
            try:
                path = path.relative_to(config_dir)
            except ValueError:
                pass
        parts = [part.lower() for part in path.parts]
    except Exception:
        return None

    for index, part in enumerate(parts):
        if part == "text" and index + 1 < len(parts):
            return parts[index + 1]
    return None


def _decode_text(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("windows-1251", errors="replace")


def _decode_xml(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("windows-1251", errors="replace")


def _raw_contains(raw: bytes, key: str) -> bool:
    if not key:
        return False
    for encoding in ("windows-1251", "utf-8"):
        try:
            needle = key.encode(encoding)
        except UnicodeEncodeError:
            continue
        if needle in raw:
            return True
    return False


def _file_identity(path) -> str:
    """Идентичность файла с разыменованием junction/symlink-алиасов."""
    path = Path(path)
    try:
        return str(path.resolve()).lower()
    except OSError:
        return str(path).lower()


def _read_bytes_cached(path: Path, cache: Optional[Dict]) -> Optional[bytes]:
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return None

    key = (str(path).lower(), stat.st_mtime_ns, stat.st_size)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if cache is not None:
        if len(cache) > 4096:
            cache.clear()
        cache[key] = raw
    return raw



def _key_attr(node: XmlNode) -> Optional[str]:
    if node.attrs.get("id"):
        return "id"
    if node.attrs.get("name"):
        return "name"
    return None


def _nearest_keyed(node: Optional[XmlNode]) -> Optional[Tuple[XmlNode, str]]:
    current = node
    while current is not None:
        if current.is_element:
            attr = _key_attr(current)
            if attr:
                return current, attr
        current = current.parent
    return None


def _counterpart(before_node: XmlNode, after: XmlDocument) -> Optional[XmlNode]:
    """Найти соответствующий узел в изменённом документе."""
    candidate = after.find(before_node.path)
    if candidate is not None and candidate.tag == before_node.tag:
        return candidate

    parent = before_node.parent
    if parent is not None:
        after_parent = after.find(parent.path)
        if after_parent is not None:
            index = 0
            for child in parent.children:
                if child.is_element and child.tag == before_node.tag:
                    if child is before_node:
                        break
                    index += 1
            matches = [
                child for child in after_parent.element_children()
                if child.tag == before_node.tag
            ]
            if index < len(matches):
                return matches[index]

    attr = _key_attr(before_node)
    if attr is not None and before_node.attrs.get(attr):
        value = before_node.attrs[attr]
        matches = [
            node for node in after.elements()
            if node.attrs.get(attr) == value
        ]
        if len(matches) == 1:
            return matches[0]

    return None


def _unique_key(
    base: str,
    before: XmlDocument,
    after: XmlDocument,
    extra: Optional[set] = None,
) -> str:
    existing = set(extra or ())
    for document in (before, after):
        for node in document.elements():
            for attr in ("id", "name"):
                value = node.attrs.get(attr)
                if value:
                    existing.add(value)

    if base not in existing:
        return base

    index = 2
    while "%s_%d" % (base, index) in existing:
        index += 1
    return "%s_%d" % (base, index)


def _collect_keys(
    source_path: Path,
    before: XmlDocument,
    after: XmlDocument,
    project_tag: str,
    warnings: List[PatchIssue],
) -> Tuple[List[XmlForkKey], List[XmlNode], List[XmlChange]]:
    """Собрать форки, новые элементы и изменения без ключа."""
    diff = diff_xml_documents(before, after)

    candidates: Dict[int, Tuple[XmlNode, str]] = {}
    added_nodes: List[Tuple[XmlNode, Tuple[Tuple[str, int], ...]]] = []

    for change in diff.changes:
        if change.kind == "xml_add_element":
            try:
                node_doc = parse_xml_document(change.node_xml)
            except XmlModelError:
                continue
            roots = node_doc.roots()
            if roots:
                added_nodes.append((roots[0], tuple(change.parent_path)))
            continue

        before_node = before.find(change.path)
        if before_node is None:
            continue
        keyed = _nearest_keyed(before_node)
        if keyed is None:
            # У блока нет id/name. Добавим id в новый файл, чтобы не
            # делать полную копию исходного XML.
            candidates.setdefault(id(before_node), (before_node, "id"))
            continue
        node, attr = keyed
        candidates[id(node)] = (node, attr)

    # Оставляем только самые верхние ключевые элементы: потомок уже
    # попадёт в клон родителя.
    kept: List[Tuple[XmlNode, str]] = []
    for node, attr in sorted(
        candidates.values(),
        key=lambda item: len(item[0].path),
    ):
        if any(
            len(parent.path) < len(node.path)
            and tuple(node.path[: len(parent.path)]) == tuple(parent.path)
            for parent, _ in kept
        ):
            continue
        kept.append((node, attr))

    keys: List[XmlForkKey] = []
    used_new: set = set()
    for before_node, attr in kept:
        old_key = before_node.attrs.get(attr)
        after_node = _counterpart(before_node, after)
        if after_node is None:
            warnings.append(
                PatchIssue(
                    code="xml_fork_counterpart_missing",
                    message=(
                        "Не найден изменённый элемент для %s[%s]"
                        % (before_node.tag, old_key)
                    ),
                    severity="warning",
                    section=before_node.display_path(),
                )
            )
            continue

        explicit = after_node.attrs.get(attr)
        if explicit and explicit != old_key:
            new_key = explicit
        else:
            base = "ace_%s" % sanitize_name(project_tag or "ace", "ace")
            if old_key:
                base = "%s_%s" % (old_key, base)
            new_key = _unique_key(
                base,
                before,
                after,
                extra=used_new,
            )
        used_new.add(new_key)
        if old_key is None:
            warnings.append(
                PatchIssue(
                    code="xml_fork_added_key",
                    message=(
                        "У изменённого элемента %s не было id/name; в новый "
                        "XML добавлен %s=\"%s\"."
                        % (
                            before_node.display_path() or before_node.tag,
                            attr,
                            new_key,
                        )
                    ),
                    severity="warning",
                    section=before_node.display_path(),
                )
            )

        clone = after_node.clone()
        clone.set_attr(attr, new_key)
        if old_key:
            _rewrite_node_values(
                clone,
                old_key,
                new_key,
                skip_attr=attr,
            )
        keys.append(
            XmlForkKey(
                old_key=old_key,
                new_key=new_key,
                key_attr=attr,
                source_path=source_path,
                before_path=before_node.path,
                node_xml=serialize_node(clone),
            )
        )

    # Добавленные элементы, не входящие в уже форкнутый родитель.
    standalone_added: List[XmlNode] = []
    for node, parent_path in added_nodes:
        if any(
            key.before_path
            and len(parent_path) >= len(key.before_path)
            and tuple(parent_path[: len(key.before_path)])
            == tuple(key.before_path)
            for key in keys
        ):
            continue
        standalone_added.append(node)

    return keys, standalone_added


# ---------------------------------------------------------------- references


def _split_csv_preserve(text: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    inside = False
    for ch in text:
        if ch == '"':
            inside = not inside
            current.append(ch)
        elif ch == "," and not inside:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _replace_token(value: str, old: str, new: str) -> Optional[str]:
    """Заменить точное значение или CSV-токен, не трогая подстроки."""
    text = str(value or "")
    if not old or old not in text:
        return None

    quoted = text.startswith('"') and text.endswith('"')
    inner = text[1:-1] if quoted else text

    parts = _split_csv_preserve(inner)
    changed = False
    result = []
    for part in parts:
        stripped = part.strip()
        leading = part[: len(part) - len(part.lstrip())]
        trailing = part[len(part.rstrip()):]
        token = stripped
        token_quoted = token.startswith('"') and token.endswith('"')
        token_inner = token[1:-1] if token_quoted else token
        if token_inner == old:
            if token_quoted:
                replacement = '"%s"' % new
            else:
                replacement = new
            result.append(leading + replacement + trailing)
            changed = True
        else:
            result.append(part)

    new_inner = ",".join(result)

    if not changed:
        # Ссылка может быть частью пути/идентификатора с разделителями
        # \ / . : - (например, ui\icons\foo). Меняем только целое слово,
        # не затрагивая foo_bar.
        pattern = _script_regex(old)
        if pattern.search(new_inner):
            new_inner = pattern.sub(new, new_inner)
            changed = True

    if not changed:
        return None

    return '"%s"' % new_inner if quoted else new_inner


def _script_regex(key: str, ignore_case: bool = False):
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(
        r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(key),
        flags,
    )


def _is_within(
    path: Tuple[Tuple[str, int], ...],
    root_path: Tuple[Tuple[str, int], ...],
) -> bool:
    return (
        len(path) >= len(root_path)
        and tuple(path[: len(root_path)]) == tuple(root_path)
    )


def _rewrite_node_values(
    node: XmlNode,
    old: str,
    new: str,
    skip_attr: Optional[str] = None,
) -> None:
    """Переписать ссылки на старый ключ внутри клона элемента."""
    if node.is_element:
        for attr, value in list(node.attrs.items()):
            if attr == skip_attr:
                continue
            replaced = _replace_token(value, old, new)
            if replaced is not None:
                node.attrs[attr] = replaced

        if not node.element_children():
            text = node.text()
            replaced = _replace_token(text, old, new)
            if replaced is not None:
                for child in list(node.children):
                    if child.is_text:
                        child.value = replaced

    for child in node.children:
        if child.is_element:
            _rewrite_node_values(child, old, new)


def _find_ltx_references(
    forest,
    keys: Sequence[XmlForkKey],
    warnings: List[PatchIssue],
    cache: Optional[Dict] = None,
) -> Dict[Tuple[Path, str, str], Tuple[str, str]]:
    """Найти LTX-значения, ссылающиеся на старые ключи."""
    result: Dict[Tuple[Path, str, str], Tuple[str, str]] = {}
    files = list(getattr(forest, "files", ()) or ())
    seen_paths = set()

    for path in files:
        path = Path(path)
        if path.suffix.lower() != ".ltx":
            continue

        raw = _read_bytes_cached(path, cache)
        if raw is None:
            continue
        if not any(
            _raw_contains(raw, key.old_key or "") for key in keys
        ):
            continue

        # VFS может содержать один файл под несколькими путями (junction,
        # регистр, алиасы MO2). Иначе ссылка находится дважды и один и то
        # же DLTX-патч генерируется повторно.
        path_identity = _file_identity(path)
        if path_identity in seen_paths:
            continue
        seen_paths.add(path_identity)

        try:
            document = parse_ltx_document(
                _decode_text(raw),
                source_path=path,
            )
        except Exception:
            continue

        for section in document.sections.values():
            for entry in section.entries:
                if entry.operation != "set" or entry.value is None:
                    continue
                value = entry.value
                new_value = value
                for key in keys:
                    if not key.old_key:
                        continue
                    replaced = _replace_token(
                        new_value,
                        key.old_key,
                        key.new_key,
                    )
                    if replaced is not None:
                        new_value = replaced
                if new_value != value:
                    result[(path, section.name, entry.key)] = (
                        value,
                        new_value,
                    )

    return result


def _find_xml_references(
    forest,
    keys: Sequence[XmlForkKey],
    warnings: List[PatchIssue],
    cache: Optional[Dict] = None,
    config_dir=None,
    source_path=None,
) -> Tuple[List[Tuple[Path, XmlDocument, XmlDocument, Tuple[XmlChange, ...]]], List[XmlForkReference]]:
    """Найти XML-атрибуты/текст, ссылающиеся на старые ключи."""
    plans = []
    references: List[XmlForkReference] = []
    seen_paths = set()

    for path in list(getattr(forest, "xml_files", ()) or ()):
        path = Path(path)

        # Локализации не трогаем: выбор языка будет отдельным режимом.
        # Иначе правка русской строки меняет и английскую.
        if _locale_of(path, config_dir) is not None:
            continue

        raw = _read_bytes_cached(path, cache)
        if raw is None:
            continue
        if not any(
            _raw_contains(raw, key.old_key or "") for key in keys
        ):
            continue

        path_identity = _file_identity(path)
        if path_identity in seen_paths:
            continue
        seen_paths.add(path_identity)

        try:
            before = parse_xml_document(
                _decode_xml(raw),
                source_path=path,
            )
        except Exception:
            continue

        changes: List[XmlChange] = []
        skip_roots = [
            key.before_path for key in keys
            if key.source_path == path
        ]
        for node in before.elements():
            if any(_is_within(node.path, root) for root in skip_roots):
                # Исходное определение форкнутого ключа должно остаться
                # нетронутым; новый ключ живёт в новом XML.
                continue
            for attr, value in list(node.attrs.items()):
                if attr in ("id", "name"):
                    # Это определение, а не ссылка. Иначе можно
                    # переименовать чужой ключ и зациклиться.
                    continue
                new_value = value
                for key in keys:
                    if not key.old_key:
                        continue
                    replaced = _replace_token(
                        new_value,
                        key.old_key,
                        key.new_key,
                    )
                    if replaced is not None:
                        new_value = replaced
                if new_value != value:
                    changes.append(
                        XmlChange(
                            kind="xml_set_attr",
                            path=node.path,
                            element=node.tag,
                            attribute=attr,
                            old_value=value,
                            new_value=new_value,
                            risk="safe",
                            line_before=node.line,
                        )
                    )
                    references.append(
                        XmlForkReference(
                            kind="xml",
                            path=path,
                            section=node.display_path(),
                            key=attr,
                            old_value=value,
                            new_value=new_value,
                            line=node.line,
                        )
                    )

            if not node.element_children():
                text = node.text()
                new_text = text
                for key in keys:
                    if not key.old_key:
                        continue
                    replaced = _replace_token(
                        new_text,
                        key.old_key,
                        key.new_key,
                    )
                    if replaced is not None:
                        new_text = replaced
                if new_text != text:
                    changes.append(
                        XmlChange(
                            kind="xml_set_text",
                            path=node.path,
                            element=node.tag,
                            old_value=text,
                            new_value=new_text,
                            risk="safe",
                            line_before=node.line,
                        )
                    )
                    references.append(
                        XmlForkReference(
                            kind="xml",
                            path=path,
                            section=node.display_path(),
                            key="#text",
                            old_value=text,
                            new_value=new_text,
                            line=node.line,
                        )
                    )

        if changes:
            after = apply_xml_changes(before, changes)
            plans.append((path, before, after, tuple(changes)))

    return plans, references


def _script_dirs(forest, config_dir) -> List[Path]:
    candidates: List[Path] = []
    bases: List[Path] = []
    if config_dir is not None:
        config_dir = Path(config_dir)
        bases.append(config_dir)
        bases.extend(list(config_dir.parents)[:5])
    scan_dir = getattr(forest, "scan_dir", None)
    if scan_dir:
        scan_dir = Path(scan_dir)
        bases.append(scan_dir)
        bases.extend(list(scan_dir.parents)[:3])

    for base in bases:
        candidates.append(base / "gamedata" / "scripts")
        candidates.append(base / "scripts")

    # MO2: VFS может быть не активен в процессе приложения, но моды
    # лежат рядом. Ищем scripts в каждом моде, чтобы не потерять ключ.
    mods_roots: List[Path] = []
    for base in bases:
        if base.name.lower() == "mods" and base.is_dir():
            mods_roots.append(base)
        for candidate in (
            base / "mods",
            base / "MO2" / "mods",
            base / "ModOrganizer" / "mods",
            base / "ModOrganizer2" / "mods",
        ):
            if candidate.is_dir():
                mods_roots.append(candidate)
    seen_mods = set()
    for mods_root in mods_roots:
        try:
            key = str(mods_root.resolve()).lower()
        except OSError:
            key = str(mods_root).lower()
        if key in seen_mods:
            continue
        seen_mods.add(key)
        try:
            mod_dirs = [
                path for path in mods_root.iterdir() if path.is_dir()
            ]
        except OSError:
            continue
        for mod_dir in mod_dirs:
            candidates.append(mod_dir / "gamedata" / "scripts")
            candidates.append(mod_dir / "scripts")
        candidates.append(mods_root.parent / "overwrite" / "scripts")

    result: List[Path] = []
    seen = set()
    for path in candidates:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen or not path.is_dir():
            continue
        seen.add(key)
        result.append(path)
    return result


def _find_script_references(
    forest,
    keys: Sequence[XmlForkKey],
    warnings: List[PatchIssue],
    config_dir=None,
    cache: Optional[Dict] = None,
) -> List[XmlForkReference]:
    """Найти старые ключи в Lua-скриптах: только предупреждение."""
    found: List[XmlForkReference] = []
    seen_files = set()

    for scripts_dir in _script_dirs(forest, config_dir):
        for path in scripts_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in (".script", ".lua"):
                continue
            try:
                key = str(path.resolve()).lower()
            except OSError:
                key = str(path).lower()
            if key in seen_files:
                continue
            seen_files.add(key)

            raw = _read_bytes_cached(path, cache)
            if raw is None:
                continue
            text = _decode_text(raw)
            for key_obj in keys:
                if not key_obj.old_key:
                    continue
                match = _script_regex(key_obj.old_key).search(text)
                case_note = ""
                if match is None:
                    match = _script_regex(
                        key_obj.old_key,
                        ignore_case=True,
                    ).search(text)
                    if match is not None:
                        case_note = " (регистр отличается)"
                if match is None:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                lines = text.splitlines()
                start = max(1, line - 5)
                end = min(len(lines), line + 5)
                excerpt = "\n".join(
                    "%5d | %s" % (number, lines[number - 1])
                    for number in range(start, end + 1)
                )
                found.append(
                    XmlForkReference(
                        kind="script",
                        path=path,
                        old_value=key_obj.old_key,
                        new_value=key_obj.new_key,
                        line=line,
                        excerpt=excerpt,
                    )
                )
                warnings.append(
                    PatchIssue(
                        code="xml_key_in_script",
                        message=(
                            "Старый XML-ключ '%s' используется в скрипте %s:%d%s. "
                            "Скрипт не изменяется автоматически — проверьте "
                            "ссылку вручную."
                            % (
                                key_obj.old_key,
                                path.name,
                                line,
                                case_note,
                            )
                        ),
                        severity="warning",
                    )
                )
    return found


# ---------------------------------------------------------------- plans


class XmlForkService:
    """Планирование и генерация форка XML-ключа."""

    def __init__(self, services):
        self.services = services
        self._byte_cache: Dict = {}

    # -------------------------------------------------- публичный вход
    def plan(
        self,
        source_path,
        baseline_text: str,
        edited_text: str,
        forest=None,
        options: Optional[PatchOptions] = None,
        project_mode: str = "root",
    ) -> XmlForkResult:
        source_path = Path(source_path)
        options = options or PatchOptions()
        config_dir = (
            Path(getattr(forest, "scan_dir"))
            if forest is not None and getattr(forest, "scan_dir", None)
            else source_path.parent
        )

        try:
            before = parse_xml_document(
                baseline_text,
                source_path=source_path,
            )
            after = parse_xml_document(
                edited_text,
                source_path=source_path,
            )
        except XmlModelError as exc:
            raise XmlForkError(str(exc))

        issues: List[PatchIssue] = []
        warnings: List[PatchIssue] = []

        locale = _locale_of(source_path, config_dir)
        if locale is not None:
            warnings.append(
                PatchIssue(
                    code="xml_locale_same_language",
                    message=(
                        "Правка локализации (%s): ключ подменяется только "
                        "в новом XML; другие языковые файлы не изменяются."
                        % locale
                    ),
                    severity="warning",
                )
            )

        try:
            initial_diff = diff_xml_documents(before, after)
        except Exception:
            initial_diff = None
        if initial_diff is not None:
            for issue in initial_diff.issues:
                issues.append(
                    PatchIssue(
                        code=issue.code,
                        message=issue.message,
                        severity=issue.severity,
                        section=(
                            "/".join(
                                tag if index == 0
                                else "%s[%d]" % (tag, index + 1)
                                for tag, index in issue.path
                            )
                            or None
                        ),
                        key=issue.attribute,
                    )
                )

        keys, added_nodes = _collect_keys(
            source_path,
            before,
            after,
            options.project_tag,
            warnings,
        )

        generated_parts: List[GeneratedPatch] = []
        new_xml_rel: Optional[Path] = None

        # 1. Новый XML с форкнутыми и добавленными элементами.
        new_nodes = [key.node_xml for key in keys]
        new_nodes.extend(serialize_node(node) for node in added_nodes)
        if new_nodes:
            new_plan = self._new_xml_plan(
                source_path,
                before,
                after,
                options,
                forest,
                config_dir,
                new_nodes,
                keys,
                warning_list=warnings,
            )
            new_xml_rel = new_plan.xml_new_file_rel
            generated_parts.append(self.services.generate_patch(new_plan))

        # 2. Ссылки в LTX -> DLTX по настройке DLTX проекта.
        dltx_mode = project_mode or "root"
        ltx_refs = (
            _find_ltx_references(
                forest,
                keys,
                warnings,
                cache=self._byte_cache,
            )
            if keys else {}
        )
        ltx_generated, ltx_plans = (
            self._ltx_reference_patches(
                ltx_refs,
                forest,
                options,
                warnings,
                dltx_mode=dltx_mode,
            )
            if ltx_refs else ([], [])
        )
        generated_parts.extend(ltx_generated)

        # 3. Ссылки в XML -> полные копии XML-файлов (без Lua).
        xml_refs, xml_references = (
            _find_xml_references(
                forest,
                keys,
                warnings,
                cache=self._byte_cache,
                config_dir=config_dir,
                source_path=source_path,
            )
            if keys else ([], [])
        )
        generated_parts.extend(
            self._xml_reference_patches(
                xml_refs,
                forest,
                options,
                config_dir,
                warnings,
            )
        )

        # 4. Ссылки в скриптах -> только предупреждение.
        script_refs = (
            _find_script_references(
                forest,
                keys,
                warnings,
                config_dir=config_dir,
                cache=self._byte_cache,
            )
            if keys else []
        )

        files = []
        checks: List[VerificationCheck] = []
        all_ok = True
        preview_parts: List[str] = []

        for part in generated_parts:
            files.extend(part.files)
            if part.verification is not None:
                checks.extend(part.verification.checks)
                issues.extend(part.verification.issues)
                if not part.verification.ok:
                    all_ok = False
            else:
                all_ok = False
            preview_parts.append("-- Файл: %s" % (
                part.files[0].relative_path if part.files else "?"
            ))
            preview_parts.append(part.text.rstrip())
            preview_parts.append("")

        if not files:
            issues.append(
                PatchIssue(
                    code="xml_fork_no_files",
                    message="Форк XML не создал ни одного файла патча",
                    severity="error",
                )
            )

        references = [
            XmlForkReference(
                kind="ltx",
                path=path,
                section=section,
                key=key,
                old_value=old_value,
                new_value=new_value,
            )
            for (path, section, key), (old_value, new_value) in ltx_refs.items()
        ] + xml_references + script_refs

        synthetic_target = PatchTarget(
            source_path=source_path,
            root_path=source_path,
            candidates=(source_path,),
            reason="Форк XML-ключа и перепривязка ссылок",
            ambiguous=False,
        )
        synthetic_changes = [
            XmlChange(
                kind="xml_add_element",
                element=key.key_attr,
                new_value=key.new_key,
                node_xml=key.node_xml,
                risk="safe",
            )
            for key in keys
        ] + [
            XmlChange(
                kind="xml_add_element",
                element="",
                node_xml=node_xml,
                risk="safe",
            )
            for node_xml in new_nodes[len(keys):]
        ]
        if not synthetic_changes and generated_parts:
            synthetic_changes.append(
                XmlChange(
                    kind="xml_fork_marker",
                    risk="safe",
                    note="Форк XML без нового ключа",
                )
            )
        synthetic_plan = PatchPlan(
            source_path=source_path,
            target=synthetic_target,
            patch_mod=options.patch_mod or "ACE Patch",
            patch_file_rel=None,
            changes=tuple(synthetic_changes),
            issues=tuple(issues),
            warnings=tuple(warnings),
            preview="\n".join(preview_parts).rstrip(),
            diff=XmlDocumentDiff(
                before=before,
                after=after,
                changes=(),
            ),
            snapshot_id=options.snapshot_id,
            encoding="windows-1251",
            format="xml_fork",
            xml_mode="xml_fork",
            xml_new_file_rel=new_xml_rel,
            xml_roots=(
                tuple(xml_root_candidates(forest, source_path))
                if forest is not None
                else (source_path,)
            ),
            xml_root_names=(
                xml_root_virtual_names(
                    config_dir,
                    tuple(xml_root_candidates(forest, source_path)),
                )
                if forest is not None
                else ()
            ),
            config_dir=config_dir,
            after_text=edited_text,
        )

        verification = VerificationResult(
            ok=all_ok and bool(files),
            checks=tuple(checks),
            issues=tuple(issues),
        )
        generated = GeneratedPatch(
            plan=synthetic_plan,
            files=tuple(files),
            text="\n".join(preview_parts).rstrip(),
            verification=verification,
        )
        return XmlForkResult(
            plan=synthetic_plan,
            generated=generated,
            keys=tuple(keys),
            references=tuple(references),
            ltx_plans=tuple(ltx_plans),
        )

    # -------------------------------------------------- новый XML
    def _new_xml_plan(
        self,
        source_path,
        before,
        after,
        options,
        forest,
        config_dir,
        node_xmls,
        keys,
        warning_list,
    ) -> PatchPlan:
        anchor = source_path
        if forest is not None:
            roots = xml_root_candidates(forest, source_path)
            if roots and source_path not in roots:
                anchor = roots[0]

        rel = xml_relative_path(forest, anchor)
        name = _new_file_name(anchor, options.project_tag)
        new_rel = Path(rel.parent) / name

        changes = tuple(
            XmlChange(
                kind="xml_add_element",
                element=key.key_attr,
                new_value=key.new_key,
                node_xml=node_xml,
                parent_path=(),
                position=1,
                risk="safe",
            )
            for key, node_xml in zip(keys, node_xmls)
        ) + tuple(
            XmlChange(
                kind="xml_add_element",
                element="",
                node_xml=node_xml,
                parent_path=(),
                position=1,
                risk="safe",
            )
            for node_xml in node_xmls[len(keys):]
        )

        return PatchPlan(
            source_path=source_path,
            target=PatchTarget(
                source_path=source_path,
                root_path=anchor,
                candidates=(anchor,),
                reason="Новый XML с форкнутыми элементами",
                ambiguous=False,
            ),
            patch_mod=options.patch_mod or "ACE Patch",
            patch_file_rel=new_rel,
            changes=changes,
            issues=(),
            warnings=tuple(warning_list),
            preview="",
            diff=XmlDocumentDiff(before=before, after=after, changes=()),
            snapshot_id=options.snapshot_id,
            encoding="windows-1251",
            format="xml",
            xml_mode="new_file",
            xml_new_file_rel=new_rel,
            xml_new_root_tag=options.xml_new_root_tag,
            config_dir=config_dir,
            patch_target_dir="configs",
        )

    # -------------------------------------------------- LTX-ссылки
    def _ltx_reference_patches(
        self,
        references,
        forest,
        options,
        warnings,
        dltx_mode: str = "root",
    ):
        """Вернуть ``(готовые патчи, DLTX-планы)`` для LTX-ссылок."""
        if not references:
            return [], []

        if dltx_mode in ("on_file", "in_place", "override"):
            return self._ltx_reference_file_patches(
                references,
                forest,
                options,
                warnings,
                dltx_mode=(
                    "override" if dltx_mode == "override" else "on_file"
                ),
            )

        # Root-режим: один итоговый DLTX-файл на корень загрузки.
        # Ссылки могут жить в разных LTX-файлах (например, в items/... и
        # в mod_system_*.ltx), но если у них один корень, все изменения
        # должны попасть в один патч. Нельзя генерировать мод-ссылку
        # отдельно от остальных: оба плана получат одно и то же имя
        # патч-файла, и он появится в предпросмотре дважды.
        plans_by_root: Dict[str, List[PatchPlan]] = {}

        def display_path(path):
            rel_paths = getattr(forest, "rel_paths", None) or {}
            try:
                value = rel_paths.get(Path(path))
            except Exception:
                value = None
            return str(value) if value else Path(path).name

        rewired_files = set()
        all_plans: List[PatchPlan] = []

        for (path, section, key), (old_value, new_value) in references.items():
            try:
                plan = self.services.patch.plan_property_patch(
                    path,
                    section,
                    key,
                    new_value,
                    forest=forest,
                    options=options,
                )
            except Exception as exc:
                warnings.append(
                    PatchIssue(
                        code="xml_fork_ltx_reference_failed",
                        message="Не удалось спланировать %s [%s]: %s"
                        % (path.name, section, exc),
                        severity="warning",
                    )
                )
                continue

            if plan.target.root_path is None:
                warnings.append(
                    PatchIssue(
                        code="xml_fork_ltx_no_root",
                        message=(
                            "Для LTX-ссылки %s [%s] не определён корень"
                            % (path.name, section)
                        ),
                        severity="warning",
                    )
                )
                continue

            note = PatchIssue(
                code="xml_fork_key_rewired",
                message=(
                    "Заменён ключ %s в [%s].%s (файл: %s)"
                    % (
                        old_value,
                        section,
                        key,
                        display_path(path),
                    )
                ),
                severity="info",
            )
            # Пометка живёт в самом плане: если уровень проекта объединит
            # его с другими планами корня, комментарий не потеряется.
            plan = replace(plan, warnings=tuple(plan.warnings) + (note,))

            # Один корень может быть представлен алиасами пути (junction,
            # регистр). Группируем по физической идентичности.
            root_key = _file_identity(plan.target.root_path)
            plans_by_root.setdefault(root_key, []).append(plan)
            all_plans.append(plan)
            rewired_files.add(_file_identity(path))

        generated: List[GeneratedPatch] = []

        for plans in plans_by_root.values():
            try:
                merged = merge_patch_plans(
                    plans,
                    patch_mod=options.patch_mod or "ACE Patch",
                )

                # Это не конфликт правок пользователя, а перепривязка
                # одной и той же XML-ссылки в разных LTX-файлах.
                # Убираем ложное предупреждение мерджера и пишем
                # человекочитаемый отчёт вместо него.
                kept_warnings = tuple(
                    warning
                    for warning in merged.warnings
                    if warning.code != "merge_key_conflict"
                )
                merged = replace(
                    merged,
                    warnings=(
                        kept_warnings
                        + (
                            (
                                PatchIssue(
                                    code="xml_fork_root_merged",
                                    message=(
                                        "Правило Root объединило "
                                        "перепривязки из %d файлов в "
                                        "один патч: %s"
                                        % (
                                            len(rewired_files),
                                            merged.patch_file_rel or "—",
                                        )
                                    ),
                                    severity="info",
                                ),
                            )
                            if len(rewired_files) > 1
                            else ()
                        )
                    ),
                )
                generated.append(self.services.generate_patch(merged))
            except Exception as exc:
                warnings.append(
                    PatchIssue(
                        code="xml_fork_ltx_merge_failed",
                        message="Не удалось объединить DLTX-ссылки: %s" % exc,
                        severity="warning",
                    )
                )
        return generated, all_plans

    def _ltx_reference_file_patches(
        self,
        references,
        forest,
        options,
        warnings,
        dltx_mode: str,
    ):
        """Перепривязать LTX-ссылки по отдельным файлам.

        On-file создаёт отдельный DLTX-патч на каждый файл, но привязывает
        его к корню загрузки. Override — полную копию файла. Так XML-режим
        соблюдает настройку DLTX проекта, а не всегда собирает один патч
        на корень.
        """
        # Ключ группы — нормализованный путь. Один физический файл,
        # найденный в VFS под алиасами, не должен дать два одинаковых
        # патча.
        by_file: Dict[str, Tuple[Path, List[Tuple[str, str, str]]]] = {}
        for (path, section, key), (_old, new_value) in references.items():
            file_key = _file_identity(path)
            entry = by_file.get(file_key)
            if entry is None:
                entry = (Path(path), [])
                by_file[file_key] = entry
            entry[1].append((section, key, new_value))

        generated: List[GeneratedPatch] = []
        all_plans: List[PatchPlan] = []

        for file_key in sorted(
            by_file,
            key=lambda item: str(by_file[item][0]).lower(),
        ):
            path, file_references = by_file[file_key]
            raw = _read_bytes_cached(path, self._byte_cache)
            if raw is None:
                warnings.append(
                    PatchIssue(
                        code="xml_fork_ltx_reference_missing",
                        message="LTX-ссылка не найдена: %s" % path,
                        severity="warning",
                    )
                )
                continue

            baseline = _decode_text(raw)
            edited = replace_values(baseline, file_references)
            if edited == baseline:
                warnings.append(
                    PatchIssue(
                        code="xml_fork_ltx_reference_unchanged",
                        message=(
                            "В %s не удалось заменить LTX-ссылку"
                            % path.name
                        ),
                        severity="warning",
                    )
                )
                continue

            try:
                if dltx_mode == "override":
                    planner = getattr(
                        self.services, "plan_ltx_override", None
                    )
                    if planner is None:
                        from core.patching.ltx.override import LtxOverrideService

                        result = LtxOverrideService().plan(
                            path,
                            baseline,
                            edited,
                            forest=forest,
                            options=options,
                            resolved=False,
                        )
                    else:
                        result = planner(
                            path,
                            baseline,
                            edited,
                            forest=forest,
                            options=options,
                            resolved=False,
                        )
                    generated.append(result.generated)
                    plan = result.plan
                    all_plans.append(plan)
                else:
                    # On-file: отдельный патч на этот файл, но привязанный
                    # к его корню загрузки, а не к самому include-файлу.
                    planner = getattr(self.services, "patch", None)
                    if planner is None or not hasattr(
                        planner,
                        "plan_document_change",
                    ):
                        from core.patching.planner import PatchPlanner

                        planner = PatchPlanner()

                    # Несколько LTX-ссылок могут принадлежать одному
                    # корню. Чтобы их патч-файлы не совпали по имени,
                    # добавляем к суффиксу короткий хеш исходного файла.
                    file_options = options
                    if len(by_file) > 1:
                        suffix = "%s_%s" % (
                            options.suffix,
                            hashlib.sha1(
                                str(path).lower().encode(
                                    "utf-8",
                                    "replace",
                                )
                            ).hexdigest()[:6],
                        )
                        file_options = replace(options, suffix=suffix)

                    plan = planner.plan_document_change(
                        path,
                        baseline,
                        edited,
                        forest=forest,
                        options=file_options,
                    )
                    all_plans.append(plan)
                    generated.append(self.services.generate_patch(plan))
            except Exception as exc:
                warnings.append(
                    PatchIssue(
                        code="xml_fork_ltx_reference_failed",
                        message=(
                            "Не удалось перепривязать LTX-ссылку %s: %s"
                            % (path.name, exc)
                        ),
                        severity="warning",
                    )
                )

        return generated, all_plans

    # -------------------------------------------------- XML-ссылки
    def _xml_reference_patches(
        self,
        references,
        forest,
        options,
        config_dir,
        warnings,
    ) -> List[GeneratedPatch]:
        """Перепривязать XML-ссылки по настройке DXML проекта.

        DXML-Lua создаёт скрипт, XML/Override — полную копию XML-файла.
        Режим выбирается настройкой проекта, а не жёстко полной копией.
        """
        requested = (options.xml_patch_mode or "auto").strip().lower()
        use_dxml = requested in ("dxml_lua", "dxml")
        generated: List[GeneratedPatch] = []

        for path, before, after, changes in references:
            diff = XmlDocumentDiff(
                before=before,
                after=after,
                changes=changes,
            )

            if use_dxml and not before.fragment:
                try:
                    from core.patching.xml.planner import _xml_script_suffix

                    roots = (
                        xml_root_candidates(forest, path)
                        if forest is not None
                        else (path,)
                    ) or (path,)
                    plan = PatchPlan(
                        source_path=path,
                        target=PatchTarget(
                            source_path=path,
                            root_path=roots[0],
                            candidates=roots,
                            reason="DXML-Lua для XML-ссылки",
                            ambiguous=False,
                        ),
                        patch_mod=options.patch_mod or "ACE Patch",
                        patch_file_rel=xml_patch_file_rel(
                            forest,
                            _xml_script_suffix(options),
                        ),
                        changes=changes,
                        issues=(),
                        warnings=(),
                        preview="",
                        diff=diff,
                        snapshot_id=options.snapshot_id,
                        encoding="utf-8-sig",
                        format="dxml",
                        xml_mode="dxml_lua",
                        xml_roots=tuple(roots),
                        xml_root_names=xml_root_virtual_names(
                            config_dir,
                            roots,
                        ),
                        config_dir=config_dir,
                        patch_target_dir="scripts",
                        after_text=serialize_xml_document(after),
                    )
                except Exception as exc:
                    warnings.append(
                        PatchIssue(
                            code="xml_fork_dxml_reference_failed",
                            message=(
                                "Не удалось построить DXML для ссылки в "
                                "%s: %s" % (path.name, exc)
                            ),
                            severity="warning",
                        )
                    )
                    continue
            else:
                if use_dxml:
                    warnings.append(
                        PatchIssue(
                            code="xml_fork_reference_fragment",
                            message=(
                                "XML-файл %s — фрагмент без корневого "
                                "контекста; ссылка изменена полной копией"
                                % path.name
                            ),
                            severity="warning",
                        )
                    )

                if before.includes:
                    warnings.append(
                        PatchIssue(
                            code="xml_fork_reference_full_copy",
                            message=(
                                "Ссылка в %s меняется полной копией "
                                "XML-файла; include-строки и "
                                "форматирование могут быть нормализованы"
                                % path.name
                            ),
                            severity="warning",
                        )
                    )

                plan = PatchPlan(
                    source_path=path,
                    target=PatchTarget(
                        source_path=path,
                        root_path=path,
                        candidates=(path,),
                        reason="Полная копия XML-ссылки",
                        ambiguous=False,
                    ),
                    patch_mod=options.patch_mod or "ACE Patch",
                    patch_file_rel=xml_relative_path(forest, path),
                    changes=changes,
                    issues=(),
                    warnings=(),
                    preview="",
                    diff=diff,
                    snapshot_id=options.snapshot_id,
                    encoding="windows-1251",
                    format="xml",
                    xml_mode="full_copy",
                    config_dir=config_dir,
                    patch_target_dir="configs",
                    after_text=serialize_xml_document(after),
                )

            try:
                generated.append(self.services.generate_patch(plan))
            except Exception as exc:
                warnings.append(
                    PatchIssue(
                        code="xml_fork_xml_reference_failed",
                        message="Не удалось сгенерировать XML-ссылку %s: %s"
                        % (path.name, exc),
                        severity="warning",
                    )
                )
        return generated

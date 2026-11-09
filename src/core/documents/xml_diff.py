# -*- coding: utf-8 -*-
"""Семантическое сравнение двух XML-документов.

Результат — не текстовый diff, а список намерений изменения: какой узел,
какой атрибут или текст, с какого значения на какое. Модуль отделяет
«изменение есть» от «изменение можно безопасно выразить патчем».

Для XML нет DLTX-операций; вместо них используются:

- ``xml_set_attr`` / ``xml_add_attr`` / ``xml_delete_attr``;
- ``xml_set_text``;
- ``xml_add_element`` / ``xml_delete_element`` / ``xml_replace_element``;
- ``xml_set_include`` (всегда блокируется: include-структура не является
  значением узла).

Узлы сопоставляются по уникальному ``id``/``name``, а при его отсутствии —
по позиции среди одноимённых элементов. Это тот же принцип, который
используется для DXML-селекторов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .xml_model import (
    XmlDocument,
    XmlModelError,
    XmlNode,
    parse_xml_document,
    serialize_node,
)


# ---------------------------------------------------------------- модели


@dataclass(frozen=True)
class XmlDiffIssue:
    """Проблема сравнения XML."""

    code: str
    message: str
    severity: str = "warning"       # warning | error
    path: Tuple[Tuple[str, int], ...] = ()
    attribute: Optional[str] = None


@dataclass(frozen=True)
class XmlChange:
    """Одно семантическое изменение XML."""

    kind: str
    path: Tuple[Tuple[str, int], ...] = ()
    element: str = ""
    attribute: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    parent_path: Tuple[Tuple[str, int], ...] = ()
    position: int = 0
    node_xml: str = ""
    risk: str = "safe"              # safe | confirm | unsupported
    line_before: int = 0
    line_after: int = 0
    note: str = ""
    source_path: Optional[str] = None

    # -------------------------------------------------- совместимость
    @property
    def section(self) -> str:
        """Строковое представление пути для общих моделей плана."""
        return _display_path(self.path) or self.element or "xml"

    @property
    def key(self) -> Optional[str]:
        if self.attribute:
            return self.attribute
        if self.kind == "xml_set_text":
            return "#text"
        return None

    @property
    def is_change(self) -> bool:
        return True


@dataclass(frozen=True)
class XmlDocumentDiff:
    """Результат сравнения XML-документов."""

    before: XmlDocument
    after: XmlDocument
    changes: Tuple[XmlChange, ...] = ()
    issues: Tuple[XmlDiffIssue, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.changes

    @property
    def has_blocking_issues(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    def blocking_issues(self) -> Tuple[XmlDiffIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity == "error"
        )

    def warnings(self) -> Tuple[XmlDiffIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity == "warning"
        )


# ---------------------------------------------------------------- helpers
_CONFIG_ATTRS = ("id", "name")


def _display_path(path: Sequence[Tuple[str, int]]) -> str:
    parts = []
    for tag, index in path:
        parts.append(tag if index == 0 else "%s[%d]" % (tag, index + 1))
    return "/".join(parts)


def _unique_map(nodes: Sequence[XmlNode], attr: str) -> Dict[str, XmlNode]:
    values: Dict[str, List[XmlNode]] = {}
    for node in nodes:
        value = node.attrs.get(attr)
        if value:
            values.setdefault(value, []).append(node)
    return {
        value: items[0]
        for value, items in values.items()
        if len(items) == 1
    }


def _match_element_children(
    before: XmlNode,
    after: XmlNode,
) -> Tuple[List[Tuple[XmlNode, XmlNode]], List[XmlNode], List[XmlNode]]:
    """Сопоставить дочерние элементы двух узлов."""
    before_nodes = before.element_children()
    after_nodes = after.element_children()

    pairs: List[Tuple[XmlNode, XmlNode]] = []
    used_before = set()
    used_after = set()

    for attr in _CONFIG_ATTRS:
        before_map = _unique_map(before_nodes, attr)
        after_map = _unique_map(after_nodes, attr)
        for value, before_node in before_map.items():
            after_node = after_map.get(value)
            if after_node is None:
                continue
            if id(before_node) in used_before or id(after_node) in used_after:
                continue
            pairs.append((before_node, after_node))
            used_before.add(id(before_node))
            used_after.add(id(after_node))

    # Остальные — по позиции среди одноимённых элементов.
    tags = []
    for node in before_nodes + after_nodes:
        if node.tag not in tags:
            tags.append(node.tag)

    for tag in tags:
        remaining_before = [
            node for node in before_nodes
            if node.tag == tag and id(node) not in used_before
        ]
        remaining_after = [
            node for node in after_nodes
            if node.tag == tag and id(node) not in used_after
        ]
        for before_node, after_node in zip(remaining_before, remaining_after):
            pairs.append((before_node, after_node))
            used_before.add(id(before_node))
            used_after.add(id(after_node))

    removed = [
        node for node in before_nodes if id(node) not in used_before
    ]
    added = [
        node for node in after_nodes if id(node) not in used_after
    ]
    return pairs, removed, added


def _node_text(node: XmlNode) -> str:
    return node.text()


def _has_element_children(node: XmlNode) -> bool:
    return bool(node.element_children())


def _compare_attributes(
    before: XmlNode,
    after: XmlNode,
) -> List[XmlChange]:
    changes: List[XmlChange] = []

    for name in sorted(set(before.attrs) | set(after.attrs)):
        old_value = before.attrs.get(name)
        new_value = after.attrs.get(name)

        if old_value == new_value:
            continue

        if old_value is None:
            kind = "xml_add_attr"
            risk = "safe"
        elif new_value is None:
            kind = "xml_delete_attr"
            risk = "confirm"
        else:
            kind = "xml_set_attr"
            risk = "safe"

        changes.append(
            XmlChange(
                kind=kind,
                path=before.path,
                element=after.tag,
                attribute=name,
                old_value=old_value,
                new_value=new_value,
                risk=risk,
                line_before=before.line if old_value is not None else 0,
                line_after=after.line if new_value is not None else 0,
            )
        )

    return changes


def _compare_nodes(
    before: XmlNode,
    after: XmlNode,
    changes: List[XmlChange],
    issues: List[XmlDiffIssue],
) -> None:
    if before.tag != after.tag:
        changes.append(
            XmlChange(
                kind="xml_replace_element",
                path=before.path,
                element=after.tag,
                old_value=before.tag,
                new_value=after.tag,
                node_xml=serialize_node(after),
                risk="confirm",
                line_before=before.line,
                line_after=after.line,
                note="Изменилось имя элемента",
            )
        )
        return

    changes.extend(_compare_attributes(before, after))

    before_has_elements = _has_element_children(before)
    after_has_elements = _has_element_children(after)

    if not before_has_elements and not after_has_elements:
        old_text = _node_text(before)
        new_text = _node_text(after)
        if old_text != new_text:
            changes.append(
                XmlChange(
                    kind="xml_set_text",
                    path=before.path,
                    element=after.tag,
                    old_value=old_text,
                    new_value=new_text,
                    risk="safe",
                    line_before=before.line,
                    line_after=after.line,
                )
            )
        return

    if before_has_elements != after_has_elements:
        changes.append(
            XmlChange(
                kind="xml_replace_element",
                path=before.path,
                element=after.tag,
                old_value=_node_text(before) or "<elements>",
                new_value=_node_text(after) or "<elements>",
                node_xml=serialize_node(after),
                risk="confirm",
                line_before=before.line,
                line_after=after.line,
                note="Смешанное содержимое: текст заменён элементами или наоборот",
            )
        )
        return

    if _node_text(before) != _node_text(after):
        changes.append(
            XmlChange(
                kind="xml_replace_element",
                path=before.path,
                element=after.tag,
                old_value=_node_text(before),
                new_value=_node_text(after),
                node_xml=serialize_node(after),
                risk="confirm",
                line_before=before.line,
                line_after=after.line,
                note="Изменён текст смешанного содержимого",
            )
        )
        return

    pairs, removed, added = _match_element_children(before, after)
    _detect_reorder(
        pairs,
        issues,
        before_nodes=before.element_children(),
        after_nodes=after.element_children(),
    )

    for before_child, after_child in pairs:
        _compare_nodes(before_child, after_child, changes, issues)

    for node in removed:
        changes.append(
            XmlChange(
                kind="xml_delete_element",
                path=node.path,
                element=node.tag,
                old_value=serialize_node(node),
                node_xml=serialize_node(node),
                risk="confirm",
                line_before=node.line,
                note="Удаление элемента затронет итоговый XML целиком",
            )
        )

    for node in added:
        position = _node_position(node)
        parent_path = before.path if node.parent is not None else ()
        changes.append(
            XmlChange(
                kind="xml_add_element",
                path=node.path,
                element=node.tag,
                new_value=serialize_node(node),
                parent_path=parent_path,
                position=position,
                node_xml=serialize_node(node),
                risk="safe",
                line_after=node.line,
            )
        )


def _node_position(node: XmlNode) -> int:
    """1-based позиция среди всех дочерних узлов родителя."""
    parent = node.parent
    if parent is None:
        return 1
    for index, child in enumerate(parent.children, 1):
        if child is node:
            return index
    return 1


def _detect_reorder(
    pairs: Sequence[Tuple[XmlNode, XmlNode]],
    issues: List[XmlDiffIssue],
    before_nodes: Optional[Sequence[XmlNode]] = None,
    after_nodes: Optional[Sequence[XmlNode]] = None,
) -> None:
    """Перестановка соседних элементов не выражается текущим патчем."""
    if len(pairs) < 2:
        return

    if before_nodes is None:
        before_nodes = [before for before, _after in pairs]
    if after_nodes is None:
        after_nodes = [after for _before, after in pairs]

    before_position = {
        id(node): index for index, node in enumerate(before_nodes)
    }
    after_position = {
        id(node): index for index, node in enumerate(after_nodes)
    }

    before_sorted_pairs = sorted(
        pairs,
        key=lambda pair: before_position.get(
            id(pair[0]),
            _node_position(pair[0]),
        ),
    )
    after_positions = [
        after_position.get(id(after), _node_position(after))
        for _before, after in before_sorted_pairs
    ]

    if after_positions != sorted(after_positions):
        issues.append(
            XmlDiffIssue(
                code="xml_reorder_unsupported",
                message=(
                    "Перестановка XML-элементов не выражается текущим "
                    "XML/DXML-патчем; измените значения без смены порядка"
                ),
                severity="error",
                path=before_sorted_pairs[0][1].path if before_sorted_pairs else (),
            )
        )


# ---------------------------------------------------------------- diff


def diff_xml_documents(
    before: XmlDocument,
    after: XmlDocument,
) -> XmlDocumentDiff:
    """Сравнить два XML-документа и вернуть семантические изменения."""
    changes: List[XmlChange] = []
    issues: List[XmlDiffIssue] = []

    if tuple(before.includes) != tuple(after.includes):
        issues.append(
            XmlDiffIssue(
                code="include_changed",
                message=(
                    "Изменение директив #include нельзя выразить XML-патчем "
                    "без изменения структуры загрузки"
                ),
                severity="error",
            )
        )

    before_roots = before.roots()
    after_roots = after.roots()

    pairs, removed, added = _match_element_children(
        _VirtualContainer(before_roots),
        _VirtualContainer(after_roots),
    )
    _detect_reorder(
        pairs,
        issues,
        before_nodes=before_roots,
        after_nodes=after_roots,
    )
    for before_node, after_node in pairs:
        _compare_nodes(before_node, after_node, changes, issues)
    for node in removed:
        changes.append(
            XmlChange(
                kind="xml_delete_element",
                path=node.path,
                element=node.tag,
                old_value=serialize_node(node),
                node_xml=serialize_node(node),
                risk="confirm",
                line_before=node.line,
                note="Удалён корневой элемент",
            )
        )
    for node in added:
        changes.append(
            XmlChange(
                kind="xml_add_element",
                path=node.path,
                element=node.tag,
                new_value=serialize_node(node),
                parent_path=(),
                position=1,
                node_xml=serialize_node(node),
                risk="safe",
                line_after=node.line,
                note="Добавлен корневой элемент",
            )
        )

    for message in before.diagnostics:
        issues.append(
            XmlDiffIssue(
                code="before_parse",
                message=message,
                severity="warning",
            )
        )
    for message in after.diagnostics:
        issues.append(
            XmlDiffIssue(
                code="after_parse",
                message=message,
                severity="warning",
            )
        )

    return XmlDocumentDiff(
        before=before,
        after=after,
        changes=tuple(changes),
        issues=tuple(issues),
    )


class _VirtualContainer(XmlNode):
    """Псевдо-родитель для сопоставления top-level элементов."""

    def __init__(self, nodes: Sequence[XmlNode]):
        super().__init__(kind="element", tag="__ace_roots__")
        self.children = list(nodes)


def diff_xml_texts(
    before_text: str,
    after_text: str,
    source_path=None,
) -> XmlDocumentDiff:
    return diff_xml_documents(
        parse_xml_document(before_text, source_path=source_path),
        parse_xml_document(after_text, source_path=source_path),
    )


# ---------------------------------------------------------------- apply


def _clone_document(document: XmlDocument) -> XmlDocument:
    return XmlDocument(
        source_path=document.source_path,
        text=document.text,
        nodes=[node.clone() for node in document.nodes],
        includes=document.includes,
        declaration=document.declaration,
        diagnostics=document.diagnostics,
        fragment=document.fragment,
        encoding=document.encoding,
    )


def _snapshot_node(xml_text: str) -> Optional[XmlNode]:
    try:
        doc = parse_xml_document(xml_text)
    except XmlModelError:
        return None
    roots = doc.roots()
    return roots[0].clone() if roots else None


def _remove_node(node: XmlNode) -> None:
    parent = node.parent
    if parent is None:
        return
    parent.children = [child for child in parent.children if child is not node]
    node.parent = None


def _remove_top_level(document: XmlDocument, node: XmlNode) -> None:
    document.nodes = [item for item in document.nodes if item is not node]
    node.parent = None


def _replace_top_level(
    document: XmlDocument,
    old: XmlNode,
    new: XmlNode,
) -> None:
    for index, item in enumerate(document.nodes):
        if item is old:
            new.parent = None
            document.nodes[index] = new
            return


def _insert_node(parent: XmlNode, node: XmlNode, position: int) -> None:
    node.parent = parent
    index = max(0, min(int(position) - 1, len(parent.children)))
    parent.children.insert(index, node)


def _replace_node(old: XmlNode, new: XmlNode) -> None:
    parent = old.parent
    if parent is None:
        return
    for index, child in enumerate(parent.children):
        if child is old:
            new.parent = parent
            parent.children[index] = new
            return


def _set_text(node: XmlNode, value: str) -> None:
    text_children = [child for child in node.children if child.is_text]
    if text_children:
        first = text_children[0]
        first.value = str(value)
        node.children = [
            child for child in node.children
            if not child.is_text or child is first
        ]
    else:
        node.children.insert(
            0,
            XmlNode(kind="text", value=str(value), parent=node),
        )


def apply_xml_changes(
    document: XmlDocument,
    changes: Sequence[XmlChange],
) -> XmlDocument:
    """Применить изменения к копии документа.

    Сначала все цели находятся по путям, затем изменения применяются к
    найденным объектам. Это важно: вставка или удаление элемента сдвигает
    пути соседей, но ссылки на уже найденные узлы остаются верными.
    """
    result = _clone_document(document)

    attr_changes: List[Tuple[XmlChange, XmlNode]] = []
    text_changes: List[Tuple[XmlChange, XmlNode]] = []
    delete_nodes: List[XmlNode] = []
    add_items: List[Tuple[XmlChange, Optional[XmlNode]]] = []
    replace_items: List[Tuple[XmlChange, XmlNode, Optional[XmlNode]]] = []

    for change in changes:
        target = result.find(change.path)

        if change.kind in ("xml_set_attr", "xml_add_attr", "xml_delete_attr"):
            if target is not None:
                attr_changes.append((change, target))
        elif change.kind == "xml_set_text":
            if target is not None:
                text_changes.append((change, target))
        elif change.kind == "xml_delete_element":
            if target is not None:
                delete_nodes.append(target)
        elif change.kind == "xml_add_element":
            parent = result.find(change.parent_path) if change.parent_path else None
            add_items.append((change, parent))
        elif change.kind == "xml_replace_element":
            if target is not None:
                replace_items.append(
                    (change, target, _snapshot_node(change.node_xml))
                )

    for change, node in attr_changes:
        if change.kind == "xml_delete_attr":
            node.remove_attr(change.attribute or "")
        else:
            node.set_attr(change.attribute or "", change.new_value or "")

    for change, node in text_changes:
        _set_text(node, change.new_value or "")

    for change, target, replacement in replace_items:
        if replacement is not None:
            if target.parent is None:
                _replace_top_level(result, target, replacement)
            else:
                _replace_node(target, replacement)

    # Удаления — от глубоких к корневым.
    delete_nodes.sort(key=lambda node: len(node.path), reverse=True)
    for node in delete_nodes:
        if node.parent is None:
            _remove_top_level(result, node)
        else:
            _remove_node(node)

    # Вставки — по возрастанию позиции, чтобы не сдвигать следующие.
    for change, parent in sorted(add_items, key=lambda item: item[0].position):
        node = _snapshot_node(change.node_xml)
        if node is None:
            continue
        if parent is None:
            result.nodes.append(node)
            node.parent = None
        else:
            _insert_node(parent, node, change.position)

    return result

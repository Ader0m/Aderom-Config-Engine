# -*- coding: utf-8 -*-
"""Семантическая модель XML-документа для патч-конвейера.

Модель не заменяет ``XmlResolver`` и не исполняет DXML. Её задача уже:
представить один XML-текст (сырой файл или resolved-вид) как дерево
элементов, атрибутов и текста, пригодное для семантического сравнения,
генерации DXML/XML-патча и проверки результата.

Правила сознательно близки к SLAXML-порту движка:

- комментарии отбрасываются;
- пустые (пробельные) текстовые узлы отбрасываются, текст обрезается;
- namespace-префиксы у тегов и обычных атрибутов отбрасываются;
- ``xmlns:*`` сохраняется как есть;
- директивы ``#include`` вынимаются из дерева и запоминаются отдельно;
- XML declaration и processing instructions сохраняются.

Модель не разворачивает ``#include``: для resolved-вида текст уже собран
резолвером, для сырого файла include-строки остаются частью документа.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from xml.parsers import expat

from core.tools.fs import read_xml_text
from core.xml.errors import XmlModelError
from core.xml.selectors import selector_segments
from core.xml.slaxml import SlaxmlError, SlaxmlParser, sanitize
from core.xml.text import escape_attr, escape_text


# ---------------------------------------------------------------- узлы


@dataclass
class XmlNode:
    """Узел XML: элемент, текст или processing instruction."""

    kind: str                     # element | text | pi
    tag: str = ""
    attrs: Dict[str, str] = field(default_factory=dict)
    value: str = ""
    children: List["XmlNode"] = field(default_factory=list)
    line: int = 0
    parent: Optional["XmlNode"] = None
    source: object = None         # LineSource/SourceRef, если известен
    path: Tuple[Tuple[str, int], ...] = ()

    # -------------------------------------------------- удобства
    @property
    def is_element(self) -> bool:
        return self.kind == "element"

    @property
    def is_text(self) -> bool:
        return self.kind == "text"

    @property
    def is_pi(self) -> bool:
        return self.kind == "pi"

    def attr(self, name: str, default: Optional[str] = None) -> Optional[str]:
        return self.attrs.get(name, default)

    def set_attr(self, name: str, value: str) -> None:
        self.attrs[str(name)] = str(value)

    def remove_attr(self, name: str) -> None:
        self.attrs.pop(name, None)

    def element_children(self) -> List["XmlNode"]:
        return [child for child in self.children if child.is_element]

    def text(self) -> str:
        """Текст только этого узла (без вложенных элементов)."""
        return "".join(
            child.value for child in self.children if child.is_text
        ).strip()

    def text_node(self) -> Optional["XmlNode"]:
        for child in self.children:
            if child.is_text:
                return child
        return None

    def clone(self) -> "XmlNode":
        copy = XmlNode(
            kind=self.kind,
            tag=self.tag,
            attrs=dict(self.attrs),
            value=self.value,
            line=self.line,
            source=self.source,
            path=self.path,
        )
        copy.children = [child.clone() for child in self.children]
        for child in copy.children:
            child.parent = copy
        return copy

    def display_path(self) -> str:
        parts = []
        for tag, index in self.path:
            parts.append(tag if index == 0 else "%s[%d]" % (tag, index + 1))
        return "/".join(parts)

    def identity(self) -> Tuple[str, str]:
        """Стабильный ключ узла внутри родителя, если он есть."""
        for name in ("id", "name"):
            value = self.attrs.get(name)
            if value:
                return (name, value)
        return ("", "")

    def canonical(self) -> Tuple:
        """Структура узла для сравнения без учёта форматирования."""
        return _canonical_node(self)


@dataclass
class XmlDocument:
    """Разобранный XML-документ."""

    source_path: Optional[Path]
    text: str
    nodes: List[XmlNode] = field(default_factory=list)   # top-level nodes
    includes: Tuple[Tuple[int, str], ...] = ()
    declaration: str = ""
    diagnostics: Tuple[str, ...] = ()
    fragment: bool = False
    encoding: str = "windows-1251"

    def roots(self) -> List[XmlNode]:
        return [node for node in self.nodes if node.is_element]

    def root(self) -> Optional[XmlNode]:
        roots = self.roots()
        return roots[0] if roots else None

    def elements(self) -> Iterator[XmlNode]:
        for node in self.nodes:
            yield from iter_elements(node)

    def element_count(self) -> int:
        return sum(1 for _ in self.elements())

    def find(self, path: Sequence[Tuple[str, int]]) -> Optional[XmlNode]:
        path = tuple(path)
        for node in self.elements():
            if node.path == path:
                return node
        return None

    def element_by_display_path(self, display: str) -> Optional[XmlNode]:
        for node in self.elements():
            if node.display_path() == display:
                return node
        return None

    @property
    def is_empty(self) -> bool:
        return not self.nodes

    def canonical(self) -> Tuple:
        """Структура, пригодная для сравнения без учёта форматирования."""
        return tuple(_canonical_node(node) for node in self.nodes)


def iter_elements(node: XmlNode) -> Iterator[XmlNode]:
    if node.is_element:
        yield node
    for child in node.children:
        yield from iter_elements(child)


def iter_nodes(node: XmlNode) -> Iterator[XmlNode]:
    yield node
    for child in node.children:
        yield from iter_nodes(child)


def _canonical_node(node: XmlNode) -> Tuple:
    if node.is_element:
        return (
            "element",
            node.tag,
            tuple(sorted(node.attrs.items())),
            tuple(_canonical_node(child) for child in node.children),
        )
    if node.is_text:
        return ("text", node.value)
    return ("pi", node.value)


# ---------------------------------------------------------------- разбор


_INCLUDE_RE = re.compile(r"#\s*include\b", re.IGNORECASE)
_DECL_RE = re.compile(r"^\s*<\?xml\b.*?\?>\s*$", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")


def _local_name(name: str) -> str:
    """Оставить локальное имя, как SLAXML; ``xmlns:*`` не трогать."""
    if name.startswith("xmlns:"):
        return name
    if ":" in name:
        return name.split(":", 1)[1]
    return name


def _extract_includes(text: str) -> Tuple[str, Tuple[Tuple[int, str], ...], str]:
    """Убрать строки #include, сохранив номера строк и declaration."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    declaration = ""
    includes: List[Tuple[int, str]] = []
    kept: List[str] = []

    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped and _DECL_RE.match(stripped):
            declaration = stripped
            kept.append("")
            continue
        if stripped.startswith("#") and _INCLUDE_RE.search(stripped):
            includes.append((number, stripped))
            kept.append("")
            continue
        kept.append(line)

    return "\n".join(kept), tuple(includes), declaration


class _ExpatBuilder:
    def __init__(self):
        self.nodes: List[XmlNode] = []
        self.stack: List[XmlNode] = []
        self.text_buffer: List[str] = []
        self.text_line = 0
        self.elements: List[XmlNode] = []
        self._child_counters: Dict[int, Dict[str, int]] = {}

    # -------------------------------------------------- expat callbacks
    def start(self, name, attrs):
        name = _local_name(name)
        node = XmlNode(
            kind="element",
            tag=name,
            attrs={},
            line=self._line,
        )
        if isinstance(attrs, (list, tuple)):
            pairs = zip(attrs[0::2], attrs[1::2])
        else:
            pairs = attrs.items()
        for key, value in pairs:
            node.attrs[_local_name(key)] = value

        parent = self.stack[-1] if self.stack else None
        node.parent = parent

        if parent is None:
            root_index = self._root_counter(name)
            self.nodes.append(node)
            root_tag = name
        else:
            self._flush_text(parent)
            root_tag = parent.path[0][0] if parent.path else name
            root_index = 0

        # path: (root_tag, root_index) + (tag, index among same-tag siblings)
        if parent is None:
            path = ((name, root_index),)
        else:
            index = self._sibling_index(parent, name)
            path = parent.path + ((name, index),)
        node.path = path
        if parent is not None:
            parent.children.append(node)
        self.stack.append(node)
        self.elements.append(node)

    @property
    def _line(self) -> int:
        return int(getattr(self, "parser", None).CurrentLineNumber or 0)

    def _root_counter(self, name: str) -> int:
        count = 0
        for node in self.nodes:
            if node.is_element and node.tag == name:
                count += 1
        return count

    def _sibling_index(self, parent: XmlNode, tag: str) -> int:
        count = 0
        for child in parent.children:
            if child.is_element and child.tag == tag:
                count += 1
        return count

    def end(self, _name):
        node = self.stack.pop()
        self._flush_text(node)

    def data(self, value):
        if self.stack:
            if not self.text_buffer:
                self.text_line = self._line
            self.text_buffer.append(value)

    def pi(self, target, data):
        if self.stack:
            self._flush_text(self.stack[-1])
            parent = self.stack[-1]
            parent.children.append(
                XmlNode(
                    kind="pi",
                    value=(target + (" " + data if data else "")),
                    line=self._line,
                    parent=parent,
                )
            )
        else:
            self.nodes.append(
                XmlNode(
                    kind="pi",
                    value=(target + (" " + data if data else "")),
                    line=self._line,
                )
            )

    def _flush_text(self, parent: XmlNode):
        if not self.text_buffer:
            return
        value = "".join(self.text_buffer).strip()
        line = self.text_line
        self.text_buffer = []
        self.text_line = 0
        if not value:
            return
        parent.children.append(
            XmlNode(kind="text", value=value, line=line, parent=parent)
        )


def _parse_with_expat(
    body: str,
    builder: _ExpatBuilder,
) -> None:
    parser = expat.ParserCreate()
    parser.ordered_attributes = True
    parser.buffer_text = True
    parser.StartElementHandler = builder.start
    parser.EndElementHandler = builder.end
    parser.CharacterDataHandler = builder.data
    parser.ProcessingInstructionHandler = builder.pi
    builder.parser = parser
    parser.Parse(body, True)


def parse_xml_document(text: str, source_path=None) -> XmlDocument:
    """Разобрать XML-текст в :class:`XmlDocument`.

    Поддерживает как полноценный документ, так и фрагмент из нескольких
    top-level элементов (для генерируемых XML-файлов). Если обычный разбор
    не удался, текст пробуется как фрагмент; это фиксируется в
    ``fragment`` и ``diagnostics``.
    """
    source_path = Path(source_path) if source_path is not None else None
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    body, includes, declaration = _extract_includes(normalized)

    # Как SLAXML: разрешаем атрибуты без кавычек.
    body = sanitize(body)

    diagnostics: List[str] = []
    builder = _ExpatBuilder()
    fragment = False

    try:
        _parse_with_expat(body, builder)
    except expat.ExpatError as exc:
        # Фрагмент: несколько top-level элементов или текст без корня.
        wrapper = "<__ace_fragment__>%s</__ace_fragment__>" % body
        builder = _ExpatBuilder()
        try:
            _parse_with_expat(wrapper, builder)
        except expat.ExpatError:
            # Игры содержат XML, которые строгий expat не принимает:
            # неэкранированные &, < в тексте, нестандартные сущности.
            # Используем тот же мягкий SLAXML-порт, что и резолвер.
            try:
                dom = SlaxmlParser().simple_dom(
                    body,
                    strip_whitespace=True,
                    parse_includes=False,
                )
                nodes = _nodes_from_slaxml(dom)
            except SlaxmlError as fallback_exc:
                raise XmlModelError(
                    "Не удалось разобрать XML%s: %s"
                    % (
                        (" (%s)" % source_path.name) if source_path else "",
                        fallback_exc,
                    )
                )
            builder.nodes = nodes
            fragment = len(
                [node for node in nodes if node.is_element]
            ) != 1
            diagnostics.append(
                "XML разобран мягким SLAXML-парсером: строгий разбор не "
                "принял файл (%s)" % exc
            )
        else:
            fragment = True
            roots = [
                node for node in builder.nodes
                if node.is_element and node.tag == "__ace_fragment__"
            ]
            if roots:
                # Разворачиваем синтетический корень.
                synthetic = roots[0]
                top_nodes: List[XmlNode] = []
                for child in synthetic.children:
                    if child.is_element or child.is_pi:
                        child.parent = None
                    top_nodes.append(child)
                # Перенумеровать пути от новых top-level элементов.
                for index, node in enumerate(top_nodes):
                    if node.is_element:
                        _reindex_paths(node, ((node.tag, index),))
                builder.nodes = top_nodes
            diagnostics.append(
                "XML разобран как фрагмент: нет единственного корневого элемента"
            )

    # Если в тексте есть #include, это важный структурный факт.
    if includes:
        diagnostics.append(
            "Найдены директивы #include: %s" % len(includes)
        )

    return XmlDocument(
        source_path=source_path,
        text=normalized,
        nodes=builder.nodes,
        includes=includes,
        declaration=declaration,
        diagnostics=tuple(diagnostics),
        fragment=fragment,
    )


def _nodes_from_slaxml(dom) -> List[XmlNode]:
    """Преобразовать DOM SLAXML в узлы модели."""
    top: List[XmlNode] = []

    def convert(raw, parent=None):
        el = raw.get("el", "")
        if not el:
            return None
        line = int(raw.get("line") or 0)
        if el.startswith("<"):
            tag = el[1:].split(" ", 1)[0]
            node = XmlNode(
                kind="element",
                tag=tag,
                attrs=dict(raw.get("attrs") or {}),
                line=line,
                parent=parent,
            )
            node.children = [
                child
                for child in (
                    convert(kid, node)
                    for kid in (raw.get("kids") or [])
                )
                if child is not None
            ]
            return node
        if el.startswith("#"):
            return XmlNode(
                kind="text",
                value=el[1:],
                line=line,
                parent=parent,
            )
        if el.startswith("?"):
            return XmlNode(
                kind="pi",
                value=el[1:],
                line=line,
                parent=parent,
            )
        return None

    for raw in dom.get("kids") or []:
        node = convert(raw, None)
        if node is not None:
            top.append(node)

    root_counters: Dict[str, int] = {}
    for node in top:
        if not node.is_element:
            continue
        index = root_counters.get(node.tag, 0)
        root_counters[node.tag] = index + 1
        _reindex_paths(node, ((node.tag, index),))
    return top


def _reindex_paths(node: XmlNode, path: Tuple[Tuple[str, int], ...]) -> None:
    node.path = path
    counters: Dict[str, int] = {}
    for child in node.children:
        if not child.is_element:
            continue
        index = counters.get(child.tag, 0)
        counters[child.tag] = index + 1
        _reindex_paths(child, path + ((child.tag, index),))


def parse_xml_file(path) -> XmlDocument:
    path = Path(path)
    return parse_xml_document(read_xml_text(path), source_path=path)


# ---------------------------------------------------------------- запись


def _inline_node(node: XmlNode) -> str:
    if node.is_pi:
        return "<?" + node.value + "?>"
    if node.is_text:
        return escape_text(node.value)
    attrs = "".join(
        ' %s="%s"' % (key, escape_attr(value))
        for key, value in node.attrs.items()
    )
    open_tag = "<%s%s" % (node.tag, attrs)
    children = [
        child for child in node.children
        if child.is_element or child.is_text or child.is_pi
    ]
    if not children:
        return open_tag + "/>"
    body = "".join(_inline_node(child) for child in children)
    return open_tag + ">" + body + "</%s>" % node.tag


def _serialize_node(
    node: XmlNode,
    indent: str,
    depth: int,
    out: List[str],
) -> None:
    pad = indent * depth

    if node.is_pi:
        out.append(pad + "<?" + node.value + "?>")
        return

    if node.is_text:
        out.append(pad + escape_text(node.value))
        return

    attrs = "".join(
        ' %s="%s"' % (key, escape_attr(value))
        for key, value in node.attrs.items()
    )
    open_tag = "<%s%s" % (node.tag, attrs)
    children = [
        child for child in node.children
        if child.is_element or child.is_text or child.is_pi
    ]

    if not children:
        out.append(pad + open_tag + "/>")
        return

    # Смешанный контент и текст сохраняем в одну строку: иначе отступы
    # добавят значимые пробельные текстовые узлы.
    if any(child.is_text for child in children):
        out.append(pad + _inline_node(node))
        return

    out.append(pad + open_tag + ">")
    for child in children:
        _serialize_node(child, indent, depth + 1, out)
    out.append(pad + "</%s>" % node.tag)


def serialize_xml_document(
    document: XmlDocument,
    indent: str = "    ",
    include_declaration: bool = True,
    include_lines: bool = True,
) -> str:
    """Сериализовать модель в читаемый XML-текст."""
    out: List[str] = []

    if include_declaration and document.declaration:
        out.append(document.declaration)

    if include_lines:
        for _line, text in document.includes:
            out.append(text)

    for node in document.nodes:
        _serialize_node(node, indent, 0, out)

    return "\n".join(out).rstrip() + "\n"


def serialize_node(node: XmlNode, indent: str = "    ") -> str:
    out: List[str] = []
    _serialize_node(node, indent, 0, out)
    return "\n".join(out)


def serialize_nodes(
    nodes: Iterable[XmlNode],
    indent: str = "    ",
) -> str:
    out: List[str] = []
    for node in nodes:
        _serialize_node(node, indent, 0, out)
    return "\n".join(out)


# ---------------------------------------------------------------- источники


def assign_sources(document: XmlDocument, line_sources: Sequence) -> None:
    """Проставить узлам владельца по номеру строки (LineSource/SourceRef)."""
    sources = list(line_sources or ())
    if not sources:
        return

    for node in document.nodes:
        for item in iter_nodes(node):
            line = item.line
            if line and 1 <= line <= len(sources):
                item.source = sources[line - 1]

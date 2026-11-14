# -*- coding: utf-8 -*-
"""Строковые таблицы игры из ``configs/text/<lang>/*.xml``.

Как это делает движок (``CStringTable::Init/Load`` из xray-monolith):

- язык берётся из ``[string_table] language`` (``localization.ltx``);
- при языке, отличном от ``eng``, сначала загружаются все файлы
  ``text/eng/*.xml``, затем файлы выбранного языка — поэтому перевод
  языка перекрывает английский, а отсутствующие строки остаются
  английскими;
- каждый файл — это ``<string_table>`` со списком
  ``<string id="..."><text>...</text></string>``;
- ``translate(id)`` возвращает текст, а если id нет в таблице — сам id.

Дополнительно поддерживаются ``#include`` внутри текстовых XML (как в
``CXml::ParseFile``). DXML для текстовых таблиц не применяется: это
строковые ресурсы, а не UI-описания.

Для карточки предмета сервис также умеет читать XML из overlay проекта
(черновики ещё не записаны на диск) и сообщать, какой файл дал
победившую строку. По этому адресу ``edit_localization_text`` меняет
только ``<text>`` нужного ``<string id>``.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from core.patching.ltx.document import parse_ltx_document
from core.tools.fs import read_text_safe, read_xml_text
from core.tools.cache import ObjectCache
from core.tools.cache import (
    file_stamp,
    path_key,
)


LOCALIZATION_VERSION = 3

# Предпочтительный язык карточки предметов: интерфейс приложения русский,
# а пользователь просит показывать текст из text/rus.
PREFERRED_LANGUAGE = "rus"
FALLBACK_LANGUAGE = "eng"

_STRING_RE = re.compile(
    r'<string\b[^>]*\bid\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</string>',
    re.DOTALL | re.IGNORECASE,
)
_TEXT_RE = re.compile(
    r'<text\b[^>]*>(.*?)</text>',
    re.DOTALL | re.IGNORECASE,
)
_INCLUDE_RE = re.compile(
    r'^\s*#\s*include\s+["\']?([^"\';]+)["\']?',
    re.IGNORECASE,
)
_COLOR_TAG_RE = re.compile(r"%c\[[^\]]*\]", re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_CDATA_RE = re.compile(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", re.DOTALL)
_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True)
class LocalizationEntry:
    """Победившее объявление строки: откуда взят текст.

    ``path`` указывает на реальный XML-файл (включая ``#include``-
    фрагмент), поэтому карточка предмета может изменить именно тот
    документ, который сейчас формирует отображаемый текст.
    """

    key: str
    text: str
    path: Optional[Path] = None
    line: int = 0
    # Текст без XML-сущностей, но с ``\\n`` и ``%c[...]``: именно его
    # показывает/правит карточка, когда пользователь входит в редактор.
    raw_text: str = ""


@dataclass(frozen=True)
class LocalizationTable:
    """Итоговая таблица строк выбранного языка (с английским fallback)."""

    language: str
    configured_language: str
    strings: dict
    files: Tuple[Path, ...] = ()
    signature: str = ""
    warnings: Tuple[str, ...] = ()
    entries: dict = field(default_factory=dict)

    def translate(self, value) -> str:
        key = clean_key(value)
        if not key:
            return ""
        text = self.strings.get(key)
        return key if text is None else text

    def has(self, value) -> bool:
        return clean_key(value) in self.strings

    def entry(self, value) -> Optional[LocalizationEntry]:
        """Объявление, которое дало текущий перевод (или ``None``)."""
        return self.entries.get(clean_key(value))


@dataclass(frozen=True)
class _Plan:
    target: str
    configured: str
    files: Tuple[Tuple[str, Tuple[Path, ...]], ...]


class LocalizationService:
    """Загружает и кэширует строковые таблицы по снимку файлов.

    ``overlay`` — ``ProjectOverlay`` (или плоская карта
    ``{path: text}``): текст берётся из черновиков проекта. Это важно для
    карточки предмета: отображаемое название/описание должно совпадать с
    тем XML, который будет сохранён, ещё до сборки патча.
    """

    def __init__(
        self,
        cache_dir=None,
        preferred_language=PREFERRED_LANGUAGE,
        max_disk_bytes=None,
    ):
        self._preferred = str(preferred_language or PREFERRED_LANGUAGE).lower()
        self._cache = ObjectCache(
            cache_dir=cache_dir,
            max_entries=4,
            max_disk_bytes=max_disk_bytes,
        )
        # Черновик проекта меняется после каждой правки: такие снимки
        # держим только в памяти, иначе получим поток 40-МБ файлов.
        self._memory_cache = ObjectCache(max_entries=8)

    # -------------------------------------------------- план загрузки

    @staticmethod
    def config_dir(forest=None, config_dir=None) -> Optional[Path]:
        value = config_dir
        if value is None and forest is not None:
            value = getattr(forest, "scan_dir", None)
        return Path(value) if value else None

    def _plan(self, forest=None, config_dir=None, overlay=None) -> _Plan:
        cfg = self.config_dir(forest, config_dir)
        if cfg is None:
            return _Plan(self._preferred, "", ())

        overlay_files = _overlay_source_files(overlay)
        configured = _read_configured_language(cfg)
        available = _available_languages(cfg, overlay_files)

        target = ""
        if self._preferred and self._preferred in available:
            target = self._preferred
        elif configured and configured.lower() in available:
            target = configured.lower()
        elif FALLBACK_LANGUAGE in available:
            target = FALLBACK_LANGUAGE
        elif available:
            target = available[0]
        else:
            target = configured.lower() or self._preferred

        languages = []
        if target and target != FALLBACK_LANGUAGE:
            languages.append(FALLBACK_LANGUAGE)
        languages.append(target or FALLBACK_LANGUAGE)

        files = []
        for lang in _dedupe(languages):
            files.append(
                (lang, _language_files(cfg, lang, overlay_files))
            )

        return _Plan(target=target, configured=configured, files=tuple(files))

    def signature(self, forest=None, config_dir=None, overlay=None) -> str:
        """Подпись снимка: язык + файлы + mtime/size + черновик проекта."""
        plan = self._plan(forest, config_dir, overlay)
        overlay_files = _overlay_source_files(overlay)
        digest = hashlib.sha1()
        digest.update(
            ("%s|%s|%s" % (
                LOCALIZATION_VERSION,
                plan.target,
                plan.configured,
            )).encode("utf-8", "replace")
        )

        for lang, files in plan.files:
            digest.update(b"\0")
            digest.update(lang.encode("ascii", "replace"))
            for path in files:
                digest.update(b"\1")
                key = path_key(path)
                digest.update(key.encode("utf-8", "replace"))
                digest.update(b"=")
                if key in overlay_files:
                    digest.update(b"overlay:")
                    digest.update(
                        hashlib.sha1(
                            overlay_files[key][1].encode(
                                "utf-8",
                                "replace",
                            )
                        ).hexdigest().encode("ascii")
                    )
                else:
                    digest.update(file_stamp(path).encode("ascii"))

        # Include-фрагменты и новые файлы могли не попасть в план по
        # glob'у, но всё ещё влиять на таблицу. Учитываем любые
        # XML-черновики проекта внутри ``text``.
        cfg = self.config_dir(forest, config_dir)
        if cfg is not None:
            text_dir = Path(cfg) / "text"
            for key, (path, text) in sorted(
                overlay_files.items(),
                key=lambda item: item[0],
            ):
                try:
                    Path(path).relative_to(text_dir)
                except (TypeError, ValueError):
                    continue
                digest.update(b"\2")
                digest.update(key.encode("utf-8", "replace"))
                digest.update(b"=")
                digest.update(
                    hashlib.sha1(
                        str(text).encode("utf-8", "replace")
                    ).hexdigest().encode("ascii")
                )

        return digest.hexdigest()

    # -------------------------------------------------- загрузка

    def load(
        self,
        forest=None,
        config_dir=None,
        log=None,
        overlay=None,
    ) -> LocalizationTable:
        plan = self._plan(forest, config_dir, overlay)
        signature = self.signature(forest, config_dir, overlay)
        cache_key = ("localization", signature)
        cache = self._memory_cache if overlay is not None else self._cache

        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        overlay_files = _overlay_source_files(overlay)
        strings = {}
        entries = {}
        loaded_files = []
        warnings = []

        for _lang, files in plan.files:
            for path in files:
                try:
                    chunks = _iter_with_includes(
                        path,
                        set(),
                        overlay_files,
                    )
                    for chunk_path, chunk_text, offset in chunks:
                        loaded_files.append(chunk_path)
                        for string_id, value, raw, line in (
                            _iter_string_entries(chunk_text)
                        ):
                            strings[string_id] = value
                            entries[string_id] = LocalizationEntry(
                                key=string_id,
                                text=value,
                                path=chunk_path,
                                line=line + offset,
                                raw_text=raw,
                            )
                except Exception as exc:
                    warnings.append("%s: %s" % (path, exc))
                    continue

        table = LocalizationTable(
            language=plan.target,
            configured_language=plan.configured,
            strings=strings,
            files=tuple(loaded_files),
            signature=signature,
            warnings=tuple(warnings),
            entries=entries,
        )
        cache.set(cache_key, table)

        if log:
            log(
                "Локализация: %s (файлов: %d, строк: %d)"
                % (plan.target or "—", len(loaded_files), len(strings))
            )
            for warning in warnings[:5]:
                log("Локализация: %s" % warning)

        return table

    def clear_memory(self):
        self._cache.clear()
        self._memory_cache.clear()


# ---------------------------------------------------------------- файлы и язык


def _dedupe(values):
    out = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def _overlay_source_files(overlay) -> dict:
    """Нормализованная карта черновиков ``{path_key: (path, text)}``."""
    if not overlay:
        return {}

    source_files = getattr(overlay, "source_files", None)
    if source_files is None:
        source_files = overlay

    try:
        items = source_files.items()
    except AttributeError:
        return {}

    result = {}
    for path, text in items:
        try:
            result[path_key(path)] = (Path(path), str(text or ""))
        except (TypeError, ValueError, OSError):
            continue
    return result


def _overlay_text(overlay_files, path) -> Optional[str]:
    item = overlay_files.get(path_key(path))
    return None if item is None else item[1]


def _available_languages(config_dir: Path, overlay_files=None) -> Tuple[str, ...]:
    root = Path(config_dir) / "text"
    result = []

    if root.is_dir():
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                try:
                    has_xml = any(child.glob("*.xml"))
                except OSError:
                    has_xml = False
                if has_xml:
                    result.append(child.name.lower())
        except OSError:
            pass

    text_key = path_key(root)
    for path, _text in (overlay_files or {}).values():
        if Path(path).suffix.lower() != ".xml":
            continue
        if path_key(Path(path).parent.parent) == text_key:
            result.append(Path(path).parent.name.lower())

    return tuple(sorted(set(result)))


def _language_files(
    config_dir: Path,
    language: str,
    overlay_files=None,
) -> Tuple[Path, ...]:
    directory = Path(config_dir) / "text" / language
    files = {}

    if directory.is_dir():
        try:
            for path in directory.glob("*.xml"):
                if path.is_file():
                    files[path_key(path)] = path
        except OSError:
            pass

    directory_key = path_key(directory)
    for path, _text in (overlay_files or {}).values():
        path = Path(path)
        if path.suffix.lower() != ".xml":
            continue
        if path_key(path.parent) != directory_key:
            continue
        files[path_key(path)] = path

    ordered = sorted(
        files.values(),
        key=lambda path: (path.name.lower(), path.name),
    )
    return tuple(ordered)


def _read_configured_language(config_dir: Path) -> str:
    """``[string_table] language`` из localization.ltx."""
    path = Path(config_dir) / "localization.ltx"
    if not path.is_file():
        return ""

    try:
        document = parse_ltx_document(read_text_safe(path), source_path=path)
        values = document.section_view("string_table").present
    except Exception:
        return ""

    for key, value in values.items():
        if str(key).lower() == "language":
            return clean_key(value)
    return ""


def _iter_with_includes(path: Path, seen, overlay_files=None):
    """Развернуть ``#include`` в порядке чтения.

    Возвращает ``(path, text, line_offset)``: ``text`` — непрерывный
    фрагмент конкретного файла, поэтому по нему можно найти владельца
    строки и отредактировать именно этот документ. Include-фрагмент
    вставляется в том месте, где он указан в родительском файле.
    """
    path = Path(path)
    key = path_key(path)
    if key in seen:
        return

    seen = set(seen)
    seen.add(key)

    overlay_text = _overlay_text(overlay_files, path)
    if overlay_text is None:
        text = read_xml_text(path)
    else:
        text = overlay_text

    lines = text.splitlines(True)
    segment = []
    segment_start = 0

    for index, line in enumerate(lines):
        match = _INCLUDE_RE.match(line.rstrip("\r\n"))
        if not match:
            segment.append(line)
            continue

        if segment:
            yield path, "".join(segment), segment_start
            segment = []

        include = match.group(1).strip().strip('"\'')
        if include:
            base = path.parent
            candidates = []
            if "*" in include or "?" in include:
                try:
                    candidates = sorted(
                        base.glob(include),
                        key=lambda item: (item.name.lower(), item.name),
                    )
                except OSError:
                    candidates = []
            else:
                candidate = Path(include)
                if candidate.is_absolute():
                    candidates = [candidate]
                else:
                    # CXml::ParseFile открывает include относительно text\<lang>.
                    candidates = [base / include]

            for candidate in candidates:
                if (
                    candidate.is_file()
                    or path_key(candidate) in (overlay_files or {})
                ):
                    yield from _iter_with_includes(
                        candidate,
                        seen,
                        overlay_files,
                    )

        segment_start = index + 1

    if segment:
        yield path, "".join(segment), segment_start


# ---------------------------------------------------------------- разбор строк


def _iter_string_entries(text: str):
    """Вернуть ``(id, текст, строка)`` из ``<string_table>``.

    Регулярное выражение, а не ElementTree: часть штатных файлов Anomaly
    не является well-formed XML с точки зрения Python, но движок их
    читает. Нам нужны только ``<string id>``/``<text>``.
    """
    text = _DECL_RE.sub("", text, count=1)
    text = _COMMENT_RE.sub("", text)

    for match in _STRING_RE.finditer(text):
        string_id = html.unescape(match.group(1)).strip()
        if not string_id:
            continue

        text_match = _TEXT_RE.search(match.group(2))
        if text_match is None:
            continue

        content = text_match.group(1)
        yield (
            string_id,
            clean_text(content),
            raw_xml_text(content),
            text.count("\n", 0, match.start()) + 1,
        )


def _parse_string_table(text: str):
    """Вернуть пары ``(id, текст)`` (совместимость со старым API)."""
    for string_id, value, _raw, _line in _iter_string_entries(text):
        yield string_id, value


# ---------------------------------------------------------------- правка строк


_STRING_BLOCK_RE = re.compile(
    r'<string\b[^>]*\bid\s*=\s*(["\'])(?P<key>[^"\']*)\1[^>]*>'
    r'(?P<body>.*?)</string>',
    re.DOTALL | re.IGNORECASE,
)


def _xml_escape(value) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


_TEXT_LINE_INDENT = "\t\t"


def _break_xml_text_lines(text, newline="\n") -> str:
    """Добавить реальные переносы после литералов ``\\n``.

    Движок считает переносом только литерал ``\\n``; реальные CR/LF в
    шрифтовом атласе имеют нулевую ширину. Поэтому такие переносы можно
    вставлять в XML для читаемости, не меняя отображение в игре.
    Отступ делаем табами: у них тоже нулевая ширина.
    """
    if "\\n" not in text:
        return text
    return text.replace(
        "\\n",
        "\\n" + newline + _TEXT_LINE_INDENT,
    )


def _format_text_value(value, original="", newline="\n") -> str:
    """Текст для ``<text>``: сохранить CDATA и записать переводы строк.

    В конфигурациях X-Ray перевод строки обычно записывается как ``\\n``
    (движок и карточка понимают эту последовательность). После каждого
    такого литерала в XML добавляется реальный перенос для читаемости;
    движок его игнорирует.
    """
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    stripped = str(original or "").strip()
    is_cdata = (
        stripped.startswith("<![CDATA[")
        and stripped.endswith("]]>")
    )

    text = text.replace("\n", "\\n")
    text = _break_xml_text_lines(text, newline)

    if is_cdata:
        return "<![CDATA[%s]]>" % text.replace("]]>", "]]]]><![CDATA[>")

    return _xml_escape(text)


def edit_localization_text(text, key, value):
    """Изменить текст ``<string id="key">``, не трогая остальной XML.

    Возвращает новый текст или ``None``, если документ не похож на
    строковую таблицу. Правка выполняется над последним объявлением
    ключа — именно его использует загрузчик строк.
    """
    key = clean_key(key)
    if not key:
        return None

    text = str(text or "")
    if not text.strip():
        return None

    matches = [
        match for match in _STRING_BLOCK_RE.finditer(text)
        if clean_key(html.unescape(match.group("key"))) == key
    ]

    newline = "\r\n" if "\r\n" in text else "\n"

    if not matches:
        return _append_localization_string(text, key, value, newline)

    match = matches[-1]
    body = match.group("body")
    text_match = _TEXT_RE.search(body)

    if text_match is None:
        replacement = '<text>%s</text>' % _format_text_value(
            value,
            newline=newline,
        )
        new_body = body.rstrip() + replacement
    else:
        content = _format_text_value(
            value,
            text_match.group(1),
            newline=newline,
        )
        new_body = (
            body[:text_match.start(1)]
            + content
            + body[text_match.end(1):]
        )

    return (
        text[:match.start("body")]
        + new_body
        + text[match.end("body"):]
    )


def _append_localization_string(text, key, value, newline="\n"):
    marker = "</string_table>"
    index = str(text).lower().rfind(marker)
    if index < 0:
        return None

    block = "\t<string id=\"%s\"><text>%s</text></string>" % (
        html.escape(key, quote=True),
        _format_text_value(value, newline=newline),
    )
    return text[:index].rstrip() + newline + block + newline + text[index:]


def clean_key(value) -> str:
    """Ключ строковой таблицы без кавычек и пробелов по краям."""
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()
    return text


def raw_xml_text(value) -> str:
    """Текст ``<text>`` без XML-сущностей, но с ``\\n`` и ``%c[...]``.

    ``CXml::Read`` возвращает значение текстового узла: CDATA-обёртка
    снимается, ``&amp;``/``&lt;`` раскрываются. Цветовые метки и
    последовательности ``\\n`` остаются частью строки — их разбирает
    движок (``CUILines``), а не XML-парсер.
    """
    text = str(value or "")

    cdata = _CDATA_RE.match(text)
    if cdata:
        text = cdata.group(1)

    return html.unescape(text)


def clean_text(value) -> str:
    """Текст для Qt-карточки: без XML-тегов и ``%c[...]``-окраски."""
    text = str(value or "")

    cdata = _CDATA_RE.match(text)
    if cdata:
        text = cdata.group(1)

    text = html.unescape(text)
    text = _BR_RE.sub(lambda _match: "\\n", text)
    text = _TAG_RE.sub("", text)
    text = _COLOR_TAG_RE.sub("", text)
    # Реальные CR/LF/TAB — форматирование XML; движок их не рисует.
    text = "".join(ch for ch in text if ord(ch) >= 0x20)
    text = text.replace("\\n", "\n")
    text = text.replace("\\t", "\t")

    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()

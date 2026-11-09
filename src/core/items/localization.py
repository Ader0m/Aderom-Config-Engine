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
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from core.patching.ltx.document import parse_ltx_document
from core.tools.fs import read_text_safe, read_xml_text
from core.tools.cache import ObjectCache
from core.tools.cache import file_stamp, path_key


LOCALIZATION_VERSION = 1

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
class LocalizationTable:
    """Итоговая таблица строк выбранного языка (с английским fallback)."""

    language: str
    configured_language: str
    strings: dict
    files: Tuple[Path, ...] = ()
    signature: str = ""
    warnings: Tuple[str, ...] = ()

    def translate(self, value) -> str:
        key = clean_key(value)
        if not key:
            return ""
        text = self.strings.get(key)
        return key if text is None else text

    def has(self, value) -> bool:
        return clean_key(value) in self.strings


@dataclass(frozen=True)
class _Plan:
    target: str
    configured: str
    files: Tuple[Tuple[str, Tuple[Path, ...]], ...]


class LocalizationService:
    """Загружает и кэширует строковые таблицы по снимку файлов."""

    def __init__(self, cache_dir=None, preferred_language=PREFERRED_LANGUAGE):
        self._preferred = str(preferred_language or PREFERRED_LANGUAGE).lower()
        self._cache = ObjectCache(cache_dir=cache_dir, max_entries=4)

    # -------------------------------------------------- план загрузки

    @staticmethod
    def config_dir(forest=None, config_dir=None) -> Optional[Path]:
        value = config_dir
        if value is None and forest is not None:
            value = getattr(forest, "scan_dir", None)
        return Path(value) if value else None

    def _plan(self, forest=None, config_dir=None) -> _Plan:
        cfg = self.config_dir(forest, config_dir)
        if cfg is None:
            return _Plan(self._preferred, "", ())

        configured = _read_configured_language(cfg)
        available = _available_languages(cfg)

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
            files.append((lang, _language_files(cfg, lang)))

        return _Plan(target=target, configured=configured, files=tuple(files))

    def signature(self, forest=None, config_dir=None) -> str:
        """Подпись снимка: язык + набор файлов + mtime/size/ctime."""
        plan = self._plan(forest, config_dir)
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
                digest.update(path_key(path).encode("utf-8", "replace"))
                digest.update(b"=")
                digest.update(file_stamp(path).encode("ascii"))

        return digest.hexdigest()

    # -------------------------------------------------- загрузка

    def load(self, forest=None, config_dir=None, log=None) -> LocalizationTable:
        plan = self._plan(forest, config_dir)
        signature = self.signature(forest, config_dir)
        cache_key = ("localization", signature)

        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        strings = {}
        loaded_files = []
        warnings = []

        for _lang, files in plan.files:
            for path in files:
                try:
                    text = _read_with_includes(path, set())
                except Exception as exc:
                    warnings.append("%s: %s" % (path, exc))
                    continue

                loaded_files.append(path)
                for string_id, value in _parse_string_table(text):
                    strings[string_id] = value

        table = LocalizationTable(
            language=plan.target,
            configured_language=plan.configured,
            strings=strings,
            files=tuple(loaded_files),
            signature=signature,
            warnings=tuple(warnings),
        )
        self._cache.set(cache_key, table)

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


# ---------------------------------------------------------------- файлы и язык


def _dedupe(values):
    out = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def _available_languages(config_dir: Path) -> Tuple[str, ...]:
    root = Path(config_dir) / "text"
    if not root.is_dir():
        return ()

    result = []
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
        return ()

    return tuple(sorted(set(result)))


def _language_files(config_dir: Path, language: str) -> Tuple[Path, ...]:
    directory = Path(config_dir) / "text" / language
    if not directory.is_dir():
        return ()

    try:
        files = [
            path for path in directory.glob("*.xml")
            if path.is_file()
        ]
    except OSError:
        return ()

    files.sort(key=lambda path: (path.name.lower(), path.name))
    return tuple(files)


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


def _read_with_includes(path: Path, seen) -> str:
    """Прочитать XML и раскрыть ``#include`` относительно каталога файла."""
    path = Path(path)
    key = path_key(path)
    if key in seen:
        return ""

    seen = set(seen)
    seen.add(key)

    text = read_xml_text(path)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output = []

    for line in lines:
        match = _INCLUDE_RE.match(line)
        if not match:
            output.append(line)
            continue

        include = match.group(1).strip().strip('"\'')
        if not include:
            continue

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
            try:
                if candidate.is_file():
                    output.append(
                        _read_with_includes(candidate, seen)
                    )
            except OSError:
                continue

    return "\n".join(output)


# ---------------------------------------------------------------- разбор строк


def _parse_string_table(text: str):
    """Вернуть пары ``(id, текст)`` из ``<string_table>``.

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

        yield string_id, clean_text(text_match.group(1))


def clean_key(value) -> str:
    """Ключ строковой таблицы без кавычек и пробелов по краям."""
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()
    return text


def clean_text(value) -> str:
    """Текст для Qt-карточки: без XML-тегов и ``%c[...]``-окраски."""
    text = str(value or "")

    cdata = _CDATA_RE.match(text)
    if cdata:
        text = cdata.group(1)

    text = html.unescape(text)
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = _COLOR_TAG_RE.sub("", text)
    text = text.replace("\\n", "\n")
    text = text.replace("\\t", "\t")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()

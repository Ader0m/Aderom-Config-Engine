# -*- coding: utf-8 -*-
"""Полнотекстовый поиск по текстовым файлам VFS.

Поиск работает по сырому собранному VFS (game + mods) и учитывает
черновики активного проекта как overlay: существующий файл заменяется
текстом черновика, новый черновик добавляется в обход.

Это не резолв LTX/XML: ищется содержимое файлов, а не итоговый текст
Resolver4. Поэтому найденная строка всегда имеет физический файл и
номер строки, который можно открыть в редакторе.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from core.tools.fs import (
    TEXT_FILE_SUFFIXES,
    normalize_path,
    read_text_safe,
    rel_path,
    scan_files,
)
from core.tools.paths import path_key


SNIPPET_LIMIT = 240
PROGRESS_EVERY = 25


class TextSearchCancelled(Exception):
    """Поиск остановлен по запросу (кооперативная отмена)."""


@dataclass(frozen=True)
class TextSearchMatch:
    """Один файл, в котором найдена входная строка."""

    path: Path
    relative_path: str
    matches: int
    first_line: int
    snippet: str


@dataclass(frozen=True)
class TextSearchResult:
    """Результат поиска: список файлов и агрегаты для статуса."""

    query: str
    files_scanned: int
    matches: tuple

    @property
    def files_matched(self) -> int:
        return len(self.matches)

    @property
    def total_matches(self) -> int:
        return sum(item.matches for item in self.matches)


def search_text_files(
    root_dir,
    query,
    *,
    suffixes=None,
    source_files: Optional[Mapping] = None,
    case_sensitive: bool = False,
    log: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> TextSearchResult:
    """Найти литеральную подстроку во всех текстовых файлах VFS.

    ``root_dir`` — каталог сканирования VFS (``forest.scan_dir``).
    ``source_files`` — карта ``{path: text}`` из overlay проекта.
    ``cancel_check`` вызывается перед чтением каждого файла и внутри
    строк; ``progress(done, total, message)`` — по мере обхода.
    """
    needle = str(query or "")
    if not needle:
        return TextSearchResult(query="", files_scanned=0, matches=())

    root = normalize_path(root_dir) if root_dir else None
    if root is None:
        return TextSearchResult(query=needle, files_scanned=0, matches=())

    allowed = {
        str(suffix).lower()
        for suffix in (suffixes or TEXT_FILE_SUFFIXES)
    }
    compare_case = bool(case_sensitive)
    needle_cmp = needle if compare_case else needle.casefold()

    files_by_key = {
        path_key(path): path
        for path in scan_files(root, allowed)
    }
    overlay_by_key = {}
    if source_files:
        root_key = _root_key(root)
        for raw_path, text in source_files.items():
            try:
                path = normalize_path(raw_path)
            except (TypeError, ValueError, OSError):
                continue
            if path.suffix.lower() not in allowed:
                continue
            key = path_key(path)
            if key in files_by_key:
                overlay_by_key[key] = str(text)
                continue
            if not _is_under(key, root_key):
                continue
            files_by_key[key] = path
            overlay_by_key[key] = str(text)

    paths = sorted(
        files_by_key.values(),
        key=lambda path: rel_path(path, root).lower(),
    )
    total = len(paths)
    found = []
    scanned = 0

    for index, path in enumerate(paths, 1):
        _raise_if_cancelled(cancel_check)

        key = path_key(path)
        text = overlay_by_key.get(key)
        if text is None:
            try:
                text = read_text_safe(path)
            except OSError as exc:
                if log is not None:
                    log(
                        "поиск по VFS: не удалось прочитать %s: %s"
                        % (path, exc)
                    )
                _report_progress(progress, index, total, path, root)
                continue

        scanned += 1
        match = _scan_text(
            path,
            text,
            needle_cmp,
            compare_case,
            root,
            cancel_check,
        )
        if match is not None:
            found.append(match)
        _report_progress(progress, index, total, path, root)

    result = TextSearchResult(
        query=needle,
        files_scanned=scanned,
        matches=tuple(found),
    )
    if log is not None:
        log(
            "поиск по VFS: проверено файлов %d, совпадений %d в %d"
            % (scanned, result.total_matches, result.files_matched)
        )
    return result


def _scan_text(
    path: Path,
    text: str,
    needle_cmp: str,
    case_sensitive: bool,
    root: Path,
    cancel_check,
) -> Optional[TextSearchMatch]:
    count = 0
    first_line = 0
    snippet = ""
    for line_number, line in enumerate(text.splitlines(), 1):
        _raise_if_cancelled(cancel_check)
        haystack = line if case_sensitive else line.casefold()
        line_matches = haystack.count(needle_cmp)
        if not line_matches:
            continue
        if not count:
            first_line = line_number
            snippet = _snippet(line, needle_cmp, case_sensitive)
        count += line_matches

    if not count:
        return None
    return TextSearchMatch(
        path=path,
        relative_path=rel_path(path, root),
        matches=count,
        first_line=first_line,
        snippet=snippet,
    )


def _snippet(line, needle_cmp, case_sensitive):
    text = str(line or "").strip()
    if len(text) <= SNIPPET_LIMIT:
        return text

    haystack = text if case_sensitive else text.casefold()
    index = haystack.find(needle_cmp)
    if index < 0:
        index = 0
    start = max(0, index - SNIPPET_LIMIT // 3)
    end = min(len(text), start + SNIPPET_LIMIT)
    start = max(0, end - SNIPPET_LIMIT)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end] + suffix


def _raise_if_cancelled(cancel_check):
    if cancel_check is not None and cancel_check():
        raise TextSearchCancelled("Поиск по VFS отменён")


def _report_progress(progress, done, total, path, root):
    if progress is None:
        return
    if done != total and done % PROGRESS_EVERY:
        return
    progress(done, total, "поиск: %s" % rel_path(path, root))


def _root_key(root) -> str:
    key = path_key(root)
    return key if key.endswith(os.sep) else key + os.sep


def _is_under(key: str, root_key: str) -> bool:
    return key == root_key.rstrip(os.sep) or key.startswith(root_key)


__all__ = [
    "TextSearchCancelled",
    "TextSearchMatch",
    "TextSearchResult",
    "search_text_files",
]

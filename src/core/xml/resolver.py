# -*- coding: utf-8 -*-
"""XmlResolver — Python-порт загрузки XML/DXML из xray-monolith.

Повторяет два этапа движка:

1. ``CXml::Load`` / ``ParseFile`` (src/xrXMLParser/xrXMLParser.cpp):
   построчная сборка ``#include`` относительно ``$game_config$``,
   включая маски ``*.xml``; затем рекурсивный разбор включённых файлов.
2. ``XMLLuaCallback`` / ``_G.COnXmlRead`` (ScriptXMLInit.cpp,
   gamedata/scripts/dxml_core.script): DXML-патчи ``on_xml_read``.

Модуль не исполняет Lua. Штатные ``modxml_*.script`` портированы в
:mod:`core.xml.dxml`; прочие modxml-скрипты отмечаются как неподдержанные.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from core.tools.fs import read_xml_text, pattern_match, normalize_path
from core.models import LineSource
from core.tools.cache import file_stamp, path_key
from core.xml.dxml import DxmlProcessor
from core.xml.slaxml import (
    SlaxmlParser,
    SourceRef,
    assign_origins,
    pretty_dom,
)


class XmlResolveError(Exception):
    """Ошибка XML-резолва (аналог R_ASSERT в движке)."""


@dataclass(frozen=True)
class XmlResolveOutput:
    """Результат XmlResolver для передачи через фасад ядра."""

    text: str
    virtual_name: str
    dxml_applied: bool = False
    notes: tuple[str, ...] = ()
    line_sources: tuple[LineSource, ...] = ()


_INCLUDE_QUOTED_RE = re.compile(r'"([^"]*)"')

# Версия логики XML-резолва: меняется при изменении формата результата.
XML_CACHE_VERSION = "xml-resolve-v2"


class XmlResolver:
    """Резолвер XML-файлов VFS: include-сборка + DXML."""

    def __init__(
        self,
        config_dir,
        scripts_dir=None,
        log=None,
        dxml=True,
        builtin_patches=True,
    ):
        if config_dir is None:
            raise XmlResolveError("Не задан каталог configs")
        self.config_dir = normalize_path(config_dir)
        if not self.config_dir.is_dir():
            raise XmlResolveError(
                "Каталог configs не найден: %s" % self.config_dir
            )
        self._log = log or (lambda msg: None)
        self._dxml_enabled = bool(dxml)
        self.notes = []

        self.scripts_dir = (
            normalize_path(scripts_dir)
            if scripts_dir is not None
            else self._detect_scripts_dir()
        )

        if self._dxml_enabled and self.scripts_dir is None:
            self.notes.append(
                "DXML: каталог scripts не найден — применена только "
                "include-сборка"
            )
            self._log(self.notes[-1])
            self._dxml_enabled = False

        if (
            self._dxml_enabled
            and self.scripts_dir is not None
            and not (Path(self.scripts_dir) / "dxml_core.script").is_file()
        ):
            self.notes.append(
                "DXML: dxml_core.script не найден — применена только "
                "include-сборка"
            )
            self._log(self.notes[-1])
            self._dxml_enabled = False

        self.dxml = DxmlProcessor(
            include_loader=self.load_include_text,
            log=self._log,
            scripts_dir=self.scripts_dir,
            builtin_patches=builtin_patches and self._dxml_enabled,
        )

        # Список include-зависимостей файла считается чтением всех
        # включённых XML. Это дорого повторять при каждом открытии уже
        # собранного файла, поэтому кэшируем зависимости и проверяем их
        # актуальность по mtime/size/ctime (см. ``_dependencies_signature``).
        self._dep_cache = {}
        self._dep_lock = threading.RLock()

        if self.dxml.unsupported_scripts:
            self.notes.append(
                "DXML: не исполнены Lua-скрипты: %s"
                % ", ".join(self.dxml.unsupported_scripts)
            )

    # -------------------------------------------------- каталоги
    def _detect_scripts_dir(self):
        candidates = [
            self.config_dir.parent / "scripts",
            self.config_dir / "scripts",
        ]
        current = self.config_dir
        for _ in range(5):
            current = current.parent
            candidates.append(current / "gamedata" / "scripts")
        for candidate in candidates:
            if (candidate / "dxml_core.script").is_file():
                return normalize_path(candidate)
        return None

    # -------------------------------------------------- имена
    def virtual_name(self, path) -> str:
        """Игровое имя файла относительно configs: ``ui\\file.xml``."""
        path = normalize_path(path)
        try:
            rel = path.relative_to(self.config_dir)
        except ValueError:
            rel = Path(path.name)
        return str(rel).replace("/", "\\")

    # -------------------------------------------------- доступ к файлам
    def _resolve_relative(self, relative: str):
        """Найти файл относительно config_dir без учёта регистра."""
        rel = str(relative).replace("\\", "/").lstrip("/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            return None
        rel = "/".join(parts)

        candidate = self.config_dir / rel
        if candidate.is_file():
            return candidate

        # Регистронезависимый поиск по сегментам (как FS движка).
        current = self.config_dir
        for part in rel.split("/"):
            if not part:
                continue
            direct = current / part
            if direct.exists():
                current = direct
                continue
            if not current.is_dir():
                return None
            match = None
            try:
                for entry in current.iterdir():
                    if entry.name.lower() == part.lower():
                        match = entry
                        break
            except OSError:
                return None
            if match is None:
                return None
            current = match
        return current if current.is_file() else None

    def load_include_text(self, relative: str):
        """Прочитать include-файл относительно config_dir; None если нет."""
        path = self._resolve_relative(relative)
        if path is None:
            return None
        try:
            return read_xml_text(path)
        except Exception as exc:
            self._log("XML: не удалось прочитать include %s: %s" % (relative, exc))
            return None

    def _all_files(self):
        """Все файлы configs (один обход на экземпляр резолвера).

        ``rglob`` по всему каталогу на каждый ``#include "*.xml"`` — слишком
        дорого при предсборке сотен XML-файлов.
        """
        cache = getattr(self, "_all_files_cache", None)
        if cache is not None:
            return cache

        cache = []
        for path in self.config_dir.rglob("*"):
            try:
                if not path.is_file():
                    continue
                rel = str(path.relative_to(self.config_dir)).replace("\\", "/")
            except (OSError, ValueError):
                continue
            cache.append(rel)
        cache.sort(key=str.lower)
        self._all_files_cache = cache
        return cache

    def _list_include_files(self, mask: str):
        """Список файлов по маске как FS.file_list(FS_ListFiles)."""
        mask = str(mask).replace("\\", "/")
        out = [
            rel.replace("/", "\\")
            for rel in self._all_files()
            if pattern_match(rel, mask)
        ]
        out.sort(key=lambda s: s.lower())
        return out

    # -------------------------------------------------- зависимости и ключ кэша
    def dependency_paths(self, path):
        """Файлы, от которых зависит резолв: сам файл + все #include.

        Используется только для ключа кэша, поэтому не собирает текст,
        а лишь рекурсивно читает директивы include.
        """
        out = []
        seen = set()
        self._collect_dependencies(
            self.virtual_name(path),
            stack=[],
            out=out,
            seen=seen,
        )
        return out

    def _collect_dependencies(self, virtual_name, stack, out, seen):
        key = str(virtual_name).replace("\\", "/").lower()
        if key in stack or key in seen:
            return

        path = self._resolve_relative(virtual_name)
        if path is None:
            return
        seen.add(key)
        out.append(path)

        try:
            text = read_xml_text(path)
        except Exception:
            return

        stack.append(key)
        try:
            for line in text.split("\n"):
                if not (line.startswith("#") and "#include" in line):
                    continue
                match = _INCLUDE_QUOTED_RE.search(line)
                if not match:
                    continue
                include = match.group(1)
                if "*.xml" in include:
                    for file_name in self._list_include_files(include):
                        self._collect_dependencies(
                            file_name,
                            stack,
                            out,
                            seen,
                        )
                else:
                    self._collect_dependencies(
                        include,
                        stack,
                        out,
                        seen,
                    )
        finally:
            stack.pop()

    def _dependencies_signature(self, path) -> str:
        cache_key = path_key(path)

        # Проверяем закэшированный список зависимостей по их меткам.
        # Если ни один файл не менялся, читать include-дерево заново не
        # нужно: это и есть основной расход при cache hit.
        with self._dep_lock:
            cached = self._dep_cache.get(cache_key)
        if cached is not None:
            entries, signature = cached
            if all(file_stamp(dep) == stamp for dep, stamp in entries):
                return signature

        seen = set()
        deps = []
        for dep in self.dependency_paths(path):
            dep = Path(dep)
            key = os.path.normcase(os.path.abspath(str(dep)))
            if key in seen:
                continue
            seen.add(key)
            deps.append((key, dep))

        digest = hashlib.sha1()
        entries = []
        for key, dep in sorted(deps, key=lambda item: item[0]):
            stamp = file_stamp(dep)
            entries.append((dep, stamp))
            digest.update(key.encode("utf-8", "replace"))
            digest.update(b"\0")
            digest.update(stamp.encode("ascii"))
            digest.update(b"\1")

        signature = digest.hexdigest()
        with self._dep_lock:
            self._dep_cache[cache_key] = (tuple(entries), signature)
        return signature

    def scripts_signature(self) -> str:
        """Подпись dxml_core.script и всех modxml_*.script."""
        sig = getattr(self, "_scripts_signature", None)
        if sig is not None:
            return sig

        if not self._dxml_enabled or self.scripts_dir is None:
            self._scripts_signature = ""
            return ""

        scripts_dir = Path(self.scripts_dir)
        files = []
        core = scripts_dir / "dxml_core.script"
        if core.is_file():
            files.append(core)
        try:
            files.extend(
                sorted(
                    scripts_dir.glob("modxml_*.script"),
                    key=lambda p: p.name.lower(),
                )
            )
        except OSError:
            pass

        digest = hashlib.sha1()
        for script in files:
            key = os.path.normcase(os.path.abspath(str(script)))
            digest.update(key.encode("utf-8", "replace"))
            digest.update(b"\0")
            digest.update(file_stamp(script).encode("ascii"))
            digest.update(b"\1")

        sig = digest.hexdigest()
        self._scripts_signature = sig
        return sig

    def cache_key(self, path, dxml=None):
        """Ключ кэша результата: файл + include-дерево + DXML-скрипты."""
        use_dxml = self._dxml_enabled if dxml is None else bool(dxml)
        return (
            XML_CACHE_VERSION,
            path_key(self.config_dir),
            path_key(path),
            self._dependencies_signature(path),
            self.scripts_signature() if use_dxml else "",
            bool(use_dxml),
        )

    def refresh(self):
        """Сбросить внутрисессионные кэши каталога/скриптов/зависимостей."""
        self._all_files_cache = None
        self._scripts_signature = None
        with self._dep_lock:
            self._dep_cache.clear()

    # -------------------------------------------------- include-сборка
    def assemble_with_origins(self, virtual_name: str):
        """Порт CXml::ParseFile: (текст, владелец каждой строки)."""
        return self._parse_file_with_origins(virtual_name, stack=[])

    def assemble(self, virtual_name: str) -> str:
        """Порт CXml::ParseFile для одного файла (только текст)."""
        return self.assemble_with_origins(virtual_name)[0]

    def _parse_file_with_origins(self, virtual_name: str, stack):
        key = str(virtual_name).replace("\\", "/").lower()
        if key in stack:
            raise XmlResolveError(
                "Циклический #include: %s" % " -> ".join(stack + [key])
            )

        path = self._resolve_relative(virtual_name)
        if path is None:
            raise XmlResolveError(
                "XML include file not found: %s" % virtual_name
            )

        try:
            text = read_xml_text(path)
        except Exception as exc:
            raise XmlResolveError(
                "Не удалось прочитать %s: %s" % (virtual_name, exc)
            )

        out = []
        origins = []
        stack.append(key)
        try:
            lines = text.split("\n")
            if lines and lines[-1] == "":
                lines.pop()

            for line_no, line in enumerate(lines, 1):
                if line.endswith("\r"):
                    line = line[:-1]

                if line.startswith("#") and "#include" in line:
                    m = _INCLUDE_QUOTED_RE.search(line)
                    if not m:
                        # _GetItem не нашёл имя — строка не попадает в вывод.
                        continue
                    inc_name = m.group(1)

                    if "*.xml" in inc_name:
                        for file_name in self._list_include_files(inc_name):
                            inc_text, inc_origins = (
                                self._parse_file_with_origins(file_name, stack)
                            )
                            out.append(inc_text)
                            origins.extend(inc_origins)
                        continue

                    inc_text, inc_origins = self._parse_file_with_origins(
                        inc_name,
                        stack,
                    )
                    out.append(inc_text)
                    origins.extend(inc_origins)
                    continue

                out.append(line + "\n")
                origins.append(
                    SourceRef(
                        file=str(virtual_name).replace("/", "\\"),
                        path=path,
                        line=line_no,
                        kind="xml",
                    )
                )
        finally:
            stack.pop()

        return "".join(out), origins

    # -------------------------------------------------- публичный API
    def resolve(self, virtual_name: str, dxml=None) -> str:
        text = self.assemble(virtual_name)
        use_dxml = self._dxml_enabled if dxml is None else bool(dxml)
        if use_dxml:
            text = self.dxml.process(
                str(virtual_name).replace("/", "\\"),
                text,
            )
        return text

    def resolve_file(self, path, dxml=None) -> str:
        return self.resolve(self.virtual_name(path), dxml=dxml)

    def resolve_output(self, path, dxml=None) -> XmlResolveOutput:
        """Полный результат: текст, владельцы строк, DXML и примечания."""
        virtual_name = self.virtual_name(path)
        assembled, origins = self.assemble_with_origins(virtual_name)

        use_dxml = self._dxml_enabled if dxml is None else bool(dxml)
        text = assembled
        dom = None

        if use_dxml:
            text, xml_obj = self.dxml.process_dom(
                virtual_name.replace("/", "\\"),
                assembled,
                origins,
            )
            if xml_obj is not None:
                dom = xml_obj.xml_table

        if dom is None:
            # DXML отключён/пропущен: DOM нужен только для форматирования
            # и построчной атрибуции по include-сборке.
            try:
                dom = SlaxmlParser(
                    include_loader=self.load_include_text
                ).simple_dom(
                    assembled,
                    strip_whitespace=True,
                    parse_includes=True,
                )
                assign_origins(dom, origins)
            except Exception as exc:
                self._log(
                    "XML: не удалось разобрать %s: %s"
                    % (virtual_name, exc)
                )
                dom = None

        line_sources = ()
        if dom is not None:
            try:
                text, refs = pretty_dom(dom)
                line_sources = tuple(
                    self._to_line_source(ref, virtual_name)
                    for ref in refs
                )
            except Exception as exc:
                self._log(
                    "XML: не удалось отформатировать %s: %s"
                    % (virtual_name, exc)
                )

        return XmlResolveOutput(
            text=text,
            virtual_name=virtual_name,
            dxml_applied=bool(use_dxml),
            notes=tuple(self.notes),
            line_sources=line_sources,
        )

    def _to_line_source(self, ref, root_virtual_name):
        """SourceRef -> доменный LineSource."""
        if ref is None:
            return None

        if ref.kind == "patch":
            return LineSource(
                kind="patch",
                display="DXML: %s" % ref.file,
                path=ref.path,
                line=ref.line,
                open_raw=True,
            )

        if str(ref.file).replace("/", "\\") == root_virtual_name:
            return LineSource(
                kind="original",
                display="",
                path=self._resolve_relative(root_virtual_name),
                open_raw=True,
            )

        return LineSource(
            kind="file",
            display=ref.file,
            path=ref.path,
            line=ref.line,
            open_raw=True,
        )

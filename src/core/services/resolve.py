# -*- coding: utf-8 -*-
"""Резолв, сборка, XML и разбор LTX.

Узкий сервис ядра; не содержит дерево, каталоги данных и патч-конвейер.
"""

from __future__ import annotations

from core.tools.external import open_in_notepad_pp, open_with_shell
from core.tools.fs import detect_config_dir


class ResolveServices:
    """Операции резолвера, сборки и XML."""

    def __init__(
        self,
        resolver,
        assembly,
        xml,
        semantic_query,
    ):
        self.resolver = resolver
        self.assembly = assembly
        self.xml = xml
        self.semantic_query = semantic_query

    # -------------------------------------------------- резолвер
    def run_resolver(self, forest, config_dir, log=None):
        """Выполнить проход Resolver4."""
        return self.resolver.run_pass(forest, config_dir, log=log)

    def assemble_text(self, root_path, forest, config_dir, log=None,
                      overlay=None):
        """Собрать текст конфигурации."""
        return self.resolver.assemble_text(
            root_path,
            forest,
            config_dir,
            log=log,
            overlay=overlay,
        )

    def assemble_resolved(self, root_path, forest, config_dir, log=None,
                          overlay=None):
        """Чистый резолв + построчный маппинг владельцев."""
        return self.resolver.assemble_resolved(
            root_path,
            forest,
            config_dir,
            log=log,
            overlay=overlay,
        )

    def invalidate_cache(self):
        """Сбросить кэш резолвера, не затрагивая остальные сервисы."""
        self.resolver.invalidate_cache()

    def invalidate_roots(self, roots):
        """Точечно инвалидировать кэш для набора корней."""
        self.resolver.invalidate_roots(roots)

    # -------------------------------------------------- сборка
    def assemble_to_file(self, root_path, forest):
        """Собрать файл в директорию сгенерированных конфигов."""
        return self.assembly.assemble_to_file(root_path, forest)

    # -------------------------------------------------- XML
    def resolve_xml(self, path, config_dir, log=None, dxml=True,
                    overlay=None):
        """Разрешить XML: include-сборка + DXML."""
        return self.xml.resolve_output(
            path,
            config_dir,
            log=log,
            dxml=dxml,
            overlay=overlay,
        )

    def resolve_xml_to_file(self, path, config_dir, log=None, dxml=True,
                            overlay=None):
        """Разрешить XML и сохранить результат в generated/xml/."""
        result = self.resolve_xml(
            path,
            config_dir,
            log=log,
            dxml=dxml,
            overlay=overlay,
        )
        return self.assembly.write_generated_xml(path, result.text)

    # -------------------------------------------------- LTX-разбор
    def ltx_section_at_line(self, text, line_number):
        """Секция, в которой находится строка (1-based), или None."""
        from core.patching.ltx.document import parse_ltx_document

        return parse_ltx_document(text).section_at_line(line_number)

    def ltx_section_bounds(self, text, section):
        """Границы секции (start, end) в 1-based нумерации."""
        from core.patching.ltx.document import parse_ltx_document

        return parse_ltx_document(text).section_bounds(section)

    def ltx_field_line(self, text, section, key):
        """Номер строки поля (последнее set-вхождение) или 0."""
        from core.patching.ltx.document import parse_ltx_document

        return parse_ltx_document(text).field_line(section, key)

    def ltx_key_from_line(self, line):
        """Ключ строки LTX без учёта DLTX-префикса и значения."""
        from core.patching.ltx.document import parse_key_line
        from core.tools.fs import strip_comments

        parsed = parse_key_line(strip_comments(str(line or "")).strip())
        if parsed is None:
            return ""
        return parsed[1].strip().lower()

    def ltx_declared_sections(self, text):
        """Все имена секций в порядке появления."""
        from core.patching.ltx.document import declared_sections

        return declared_sections(text)

    def ltx_edit_section(
        self,
        text,
        section,
        key,
        action,
        new_line=None,
        after_key=None,
    ):
        """Точечно изменить строку секции, сохранив переводы строк."""
        from core.patching.ltx.document import edit_section_text

        return edit_section_text(
            text,
            section,
            key,
            action,
            new_line=new_line,
            after_key=after_key,
        )

    # -------------------------------------------------- внешние инструменты
    def open_external_editor(self, path):
        """Открыть файл во внешнем редакторе."""
        return self.open_notepad(path)

    def open_notepad(self, path):
        """Открыть файл в Notepad++."""
        return open_in_notepad_pp(path)

    def open_with_shell(self, path):
        """Открыть файл приложением Windows по умолчанию."""
        return open_with_shell(path)

    def detect_config_dir(self, candidates):
        """Определить каталог конфигов."""
        return detect_config_dir(candidates)


__all__ = ["ResolveServices"]

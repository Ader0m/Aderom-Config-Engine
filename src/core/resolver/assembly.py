# src/core/assembly_service.py
# -*- coding: utf-8 -*-
"""
AssemblyService — сервис ядра для сборки конфигурационных файлов.

Отвечает за:
- построение карты источников (source_map);
- сборку файлов в директорию сгенерированных конфигов.

НЕ отвечает за:
- поиск и подключение резолвера (это делает ResolverService);
- парсинг файлов (это делает внешний резолвер).
"""

from pathlib import Path

from core.resolver.attribution import build_source_map
from core.tools.text import safe_filename_component


class AssemblyService:
    """Сервис ядра для сборки конфигурационных файлов."""

    def __init__(self, resolver_service, generated_dir):
        """
        Args:
            resolver_service: ResolverService — для получения адаптера.
            generated_dir: Директория для сгенерированных файлов.
        """
        self._resolver = resolver_service
        self._generated_dir = Path(generated_dir)

    # -------------------------------------------------- карта источников

    def source_map(self, forest):
        """
        Построить карту источников для дерева.

        Это бывший метод фасада `source_map`.
        """
        return build_source_map(forest)

    # -------------------------------------------------- сборка в файл

    def assemble_to_file(self, root_path, forest):
        """
        Собрать файл и сохранить его в директорию сгенерированных конфигов.

        Это бывший метод фасада `assemble_to_file`.
        Теперь принимает `forest` вместо `source_map`,
        так как сервис сам строит карту источников.

        Args:
            root_path: Путь к корневому файлу.
            forest: Построенное дерево файлов.

        Returns:
            Path: Путь к сгенерированному файлу.

        Raises:
            RuntimeError: Если резолвер не загружен.
        """
        if self._resolver is None:
            raise RuntimeError("Резолвер не загружен")

        config_dir = getattr(forest, "scan_dir", None)
        return self._resolver.assemble_to_file(
            root_path,
            forest,
            self._generated_dir,
            config_dir=config_dir,
        )

    # -------------------------------------------------- XML-сборка
    def write_generated_xml(self, root_path, text):
        """Сохранить результат XmlResolver в generated/xml/ (UTF-8)."""
        out_dir = self._generated_dir / "xml"
        out_dir.mkdir(parents=True, exist_ok=True)

        rp = Path(root_path)

        out = out_dir / (
            "%s__%s__resolved.xml"
            % (
                safe_filename_component(rp.parent.name),
                safe_filename_component(rp.stem),
            )
        )
        out.write_text(
            text,
            encoding="windows-1251",
            errors="replace",
        )
        return out

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

from .fs import build_source_map


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
        adapter = self._resolver.get_adapter()
        if adapter is None:
            raise RuntimeError("Резолвер не загружен")

        sm = self.source_map(forest)
        return adapter.assemble_to_file(root_path, self._generated_dir, sm)
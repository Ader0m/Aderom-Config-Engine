# -*- coding: utf-8 -*-
"""Публичные API-контракты SDK для модулей.

Модуль не должен знать, как устроены `core` и `ui`. Он видит только:

- :class:`CoreApi` — возможности ядра (дерево, резолвер, сборка, граф);
- :class:`UiApi` — возможности платформы (меню, уведомления, редактор);
- :class:`TaskApi` — запуск фоновых задач.

Конкретные реализации живут в :mod:`sdk.adapters` и создаются
:class:`sdk.contracts.ModuleContext`. Благодаря этому модуль зависит
только от SDK, а не от классов ядра или виджетов PyQt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence


# ---------------------------------------------------------------- модели данных


@dataclass(frozen=True)
class TreeSummary:
    """Краткая сводка по построенному дереву."""

    roots: int
    total_files: int
    system_tree_files: int
    resolver_sections: Optional[int] = None


@dataclass(frozen=True)
class GraphMatch:
    """Одно совпадение в графе связей секции."""

    edge_type: str
    line_number: int
    line_text: str = ""


@dataclass(frozen=True)
class GraphNode:
    """Узел графа связей в терминах SDK, а не внутренних моделей ядра."""

    display_name: str
    file_path: Optional[Path]
    first_match_line: int
    matches: tuple[GraphMatch, ...] = field(default_factory=tuple)
    raw_ref: Optional[str] = None

    @property
    def matches_count(self) -> int:
        return len(self.matches)

    @property
    def edge_types(self) -> tuple[str, ...]:
        return tuple(sorted({m.edge_type for m in self.matches}))

    @property
    def is_texture(self) -> bool:
        return self.raw_ref is not None


@dataclass(frozen=True)
class SectionGraph:
    """Граф связей секции."""

    section_name: str
    root: GraphNode
    children: tuple[GraphNode, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------- контракты


class CoreApi(Protocol):
    """API ядра, доступное модулю через ``ctx.services``."""

    # --- конфигурация и дерево -------------------------------------------

    def detect_config_dir(self, candidates: Sequence[Path]) -> Optional[Path]:
        """Найти каталог конфигов игровой VFS среди кандидатов."""

    def find_system_ltx(self, selected_dir: Path) -> Optional[Path]:
        """Найти ``system.ltx`` в выбранном каталоге."""

    def resolve_scan_dir(self, config_dir: Path) -> Optional[Path]:
        """Вернуть каталог, от которого нужно строить дерево."""

    def build_tree(
        self,
        scan_dir: Path,
        system_root: Path,
        log: Optional[Callable[[str], None]] = None,
        clean: bool = False,
    ):
        """Построить дерево и вернуть непрозрачный handle для SDK."""

    def tree_summary(self, forest) -> TreeSummary:
        """Вернуть сводку по построенному дереву."""

    def relative_path(self, forest, path: Path) -> str:
        """Относительный путь файла в дереве."""

    def patch_target(self, forest, path: Path) -> Optional[Path]:
        """Базовый файл, к которому применяется DLTX-патч."""

    def read_text(self, path: Path) -> str:
        """Прочитать текстовый файл средствами ядра."""

    # --- резолвер и сборка ------------------------------------------------

    def run_resolver(
        self,
        forest,
        config_dir: Path,
        log: Optional[Callable[[str], None]] = None,
    ):
        """Выполнить проход резолвера."""

    def assemble_text(
        self,
        root_path: Path,
        forest,
        config_dir: Path,
        log: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Собрать итоговый текст файла так, как его видит игра."""

    def assemble_to_file(self, root_path: Path, forest) -> Path:
        """Собрать файл в каталог сгенерированных конфигов."""

    # --- граф связей и внешние инструменты -------------------------------

    def build_section_graph(self, forest, section_name: str) -> SectionGraph:
        """Построить граф связей для секции."""

    def open_external_editor(self, path: Path) -> tuple[bool, str]:
        """Открыть файл во внешнем редакторе. Возвращает ``(ok, message)``."""


class UiApi(Protocol):
    """API платформы, доступное модулю через ``ctx.ui``."""

    # --- регистрация возможностей модуля ---------------------------------

    def register_menu(
        self,
        menu_title: str,
        label: str,
        callback: Callable,
        shortcut: Optional[str] = None,
    ) -> None:
        """Добавить пункт меню."""

    def register_tree_context_action(self, label: str, handler: Callable) -> None:
        """Добавить действие в контекстное меню дерева."""

    def register_open_handler(self, handler) -> None:
        """Зарегистрировать обработчик открытия файлов."""

    # --- уведомления и запросы --------------------------------------------

    def log(self, message: str) -> None:
        """Записать сообщение в журнал."""

    def set_status(self, message: str, timeout: int = 0) -> None:
        """Показать сообщение в статусной строке."""

    def show_error(self, text: str) -> None:
        """Показать ошибку пользователю."""

    def ask_directory(self, initial: str = "") -> Optional[str]:
        """Запросить каталог у пользователя."""

    def ask_text(self, prompt: str, initial: str = "") -> str:
        """Запросить строку у пользователя."""

    def show_text(self, text: str, label: str, readonly: bool = True) -> None:
        """Показать текст в редакторе/просмотрщике платформы."""

    def show_module_window(self, key: str, window) -> None:
        """Показать окно, созданное модулем, и передать его платформе."""

    def close_module_window(self, key: str) -> None:
        """Закрыть окно модуля по ключу."""

    def copy_to_clipboard(self, text: str) -> None:
        """Поместить текст в буфер обмена."""

    # --- команды платформы ------------------------------------------------

    def open_raw(self, path: Path) -> None:
        """Открыть файл в редакторе без обработчиков модуля."""

    def open_at_line(self, path: Path, line_number: int) -> None:
        """Открыть файл и перейти к строке."""

    def undo(self) -> None:
        """Отменить действие в редакторе."""

    def redo(self) -> None:
        """Повторить действие в редакторе."""

    def find_in_editor(self) -> None:
        """Открыть поиск по текущему файлу."""

    def sort_file_tree_by_files_desc(self) -> None:
        """Отсортировать дерево по количеству файлов по убыванию."""


class TaskApi(Protocol):
    """API фоновых задач, доступное модулю через ``ctx.tasks``."""

    def run(
        self,
        fn: Callable,
        on_done: Optional[Callable] = None,
        on_log: Optional[Callable] = None,
    ):
        """Запустить ``fn(log)`` в фоне."""

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


class LineSource(Protocol):
    """Владелец строки итогового текста (см. core.models.LineSource)."""

    kind: str
    display: str
    path: Optional[Path]
    line: int
    open_raw: bool


@dataclass(frozen=True)
class TreeSummary:
    """Краткая сводка по построенному дереву."""

    roots: int
    total_files: int
    system_tree_files: int
    resolver_sections: Optional[int] = None
    xml_files: int = 0


@dataclass(frozen=True)
class GraphMatch:
    """Одно совпадение в графе связей секции."""

    edge_type: str
    line_number: int
    line_text: str = ""
    # Второй участник связи: для наследования — имя родителя или
    # дочерней секции.
    related: Optional[str] = None


@dataclass(frozen=True)
class GraphNode:
    """Узел графа связей в терминах SDK, а не внутренних моделей ядра."""

    display_name: str
    file_path: Optional[Path]
    first_match_line: int
    matches: tuple[GraphMatch, ...] = field(default_factory=tuple)
    raw_ref: Optional[str] = None
    resolved: bool = False
    # Полный путь к бинарному ресурсу (dds/ogf). file_path при этом
    # указывает на файл, где записана ссылка (icons_texture/visual).
    target_path: Optional[Path] = None
    # Реальный путь на диске для запуска внешней программы. Может
    # отличаться от target_path, если тот получен через VFS (MO2/usvfs)
    # или собран junction-ами.
    physical_path: Optional[Path] = None
    # Объявление связанной секции (родителя): файл, строка и признак
    # того, что строка относится к результату резолва.
    related_file_path: Optional[Path] = None
    related_line: Optional[int] = None
    related_resolved: bool = False

    @property
    def matches_count(self) -> int:
        return len(self.matches)

    @property
    def edge_types(self) -> tuple[str, ...]:
        return tuple(sorted({m.edge_type for m in self.matches}))

    @property
    def primary_edge_type(self) -> str:
        """Тип связи первой строки совпадения (для маркера в окне)."""
        if self.matches:
            return self.matches[0].edge_type
        return "declaration"

    @property
    def related_name(self) -> Optional[str]:
        """Имя второго участника связи (родитель/дочерняя секция)."""
        for match in self.matches:
            related = getattr(match, "related", None)
            if related:
                return related
        return None

    @property
    def is_texture(self) -> bool:
        return "texture_reference" in self.edge_types

    @property
    def is_visual(self) -> bool:
        return "visual_reference" in self.edge_types

    @property
    def is_asset(self) -> bool:
        """Узел ссылается на бинарный ресурс (текстура/модель)."""
        return self.is_texture or self.is_visual


@dataclass(frozen=True)
class SectionGraph:
    """Граф связей секции."""

    section_name: str
    root: GraphNode
    children: tuple[GraphNode, ...] = field(default_factory=tuple)
    # Заполняется только в режиме VFS: связи, отсутствующие в итоговом дереве.
    lost_children: tuple[GraphNode, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class XmlResolveResult:
    """Результат XML-резолва (include-сборка + DXML)."""

    text: str
    virtual_name: str
    dxml_applied: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
    line_sources: tuple[LineSource, ...] = field(default_factory=tuple)


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

    # --- фоновая предсборка ----------------------------------------------

    def start_prebuild(self, forest, config_dir: Path) -> None:
        """Запустить фоновую сборку всех LTX-файлов дерева."""

    def prebuild_status(self) -> dict:
        """Текущий статус фоновой сборки: state/done/total/error."""

    def wait_prebuild(self, log: Optional[Callable[[str], None]] = None) -> None:
        """Дождаться окончания фоновой сборки."""

    def stop_prebuild(self) -> None:
        """Остановить фоновую сборку."""

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

    def assemble_resolved(
        self,
        root_path: Path,
        forest,
        config_dir: Path,
        log: Optional[Callable[[str], None]] = None,
    ) -> tuple[str, list[Optional[LineSource]]]:
         """Собрать чистый резолв + маппинг (source_per_line)."""

    def resolve_xml(
        self,
        path: Path,
        config_dir: Path,
        log: Optional[Callable[[str], None]] = None,
        dxml: bool = True,
    ) -> XmlResolveResult:
        """Разрешить XML: #include-сборка + DXML-патчи."""

    def resolve_xml_to_file(
        self,
        path: Path,
        config_dir: Path,
        log: Optional[Callable[[str], None]] = None,
        dxml: bool = True,
    ) -> Path:
        """Разрешить XML и сохранить результат в generated/xml/."""

    # --- граф связей и внешние инструменты -------------------------------

    def build_section_graph(
        self,
        forest,
        section_name: str,
        vfs_mode: bool = False,
        log: Optional[Callable[[str], None]] = None,
    ) -> SectionGraph:
        """Построить граф связей для секции.

        ``vfs_mode=True`` — обойти всю VFS, не ограничиваясь LtxForest,
        и сравнить её с сырым итоговым деревом без запуска резолвера.
        В ``SectionGraph.lost_children`` попадут связи, которых нет в
        итоговом дереве.
        """

    def open_external_editor(self, path: Path) -> tuple[bool, str]:
        """Открыть файл во внешнем редакторе. Возвращает ``(ok, message)``."""

    def open_with_shell(self, path: Path) -> tuple[bool, str]:
        """Открыть файл приложением Windows по умолчанию.

        Если ассоциация не настроена, показывается системный диалог
        «Открыть с помощью…». Возвращает ``(ok, message)``.
        """


class UiApi(Protocol):
    """API платформы, доступное модулю через ``ctx.ui``."""

    # --- регистрация возможностей модуля ---------------------------------

    def register_menu(
        self,
        menu_title: str,
        label: str,
        callback: Callable,
        shortcut: Optional[str] = None,
        action: Optional[str] = None,
    ) -> None:
        """Добавить пункт меню.

        Если задан ``action``, нажатие сначала проходит через ``dispatch``:
        другой модуль может перехватить действие и не дать вызвать ``callback``.
        """

    def register_tree_context_action(
        self,
        label: str,
        handler: Callable,
        predicate: Optional[Callable[[Path], bool]] = None,
    ) -> None:
        """Добавить действие в контекстное меню дерева.

        ``predicate(path)`` — необязательный фильтр видимости действия
        для конкретного файла (например, только для ``.xml``).
        """

    def register_open_handler(self, handler) -> None:
        """Зарегистрировать обработчик открытия файлов."""

    def register_shortcut(
        self,
        key: str,
        callback: Callable,
        scope: str = "global",
        action: Optional[str] = None,
    ) -> None:
        """Зарегистрировать горячую клавишу.

        ``scope``: ``global``, ``editor`` или ``tree``.
        ``action`` позволяет перехватить нажатие через ``dispatch_action``.
        Без ``action`` всё равно рассылается общее действие
        :attr:`sdk.events.Events.UI_SHORTCUT`.
        """

    def register_mouse_binding(
        self,
        button,
        modifiers,
        callback: Callable,
        scope: str = "global",
        event: str = "press",
        action: Optional[str] = None,
    ) -> None:
        """Зарегистрировать мышиную привязку.

        ``button``: ``left``/``right``/``middle`` или Qt-кнопка.
        ``modifiers``: ``Ctrl+Shift``, список или Qt-флаги.
        ``event``: ``press``/``release``/``double``/``move``.
        Callback получает QMouseEvent; truthy-результат поглощает событие.
        Без ``action`` всё равно рассылается общее действие
        :attr:`sdk.events.Events.UI_MOUSE`.
        """

    def register_action_handler(
        self,
        action: str,
        handler: Callable,
        priority: int = 0,
    ):
        """Подписаться на перехватываемое UI-действие.

        Чем выше ``priority``, тем раньше вызывается обработчик.
        Truthy-результат останавливает цепочку.
        """

    def dispatch_action(self, action: str, payload=None) -> bool:
        """Разослать UI-действие. Возвращает ``True``, если оно перехвачено."""

    def open_resolved_tab(
        self,
        tab_id: str,
        title: str,
        text: str,
        line_sources: Sequence[Optional[LineSource]],
        root_path: Path,
        line_number: Optional[int] = None,
    ) -> None:
        """Открыть вкладку резолв-вида с панелью источников."""

    def open_xml_resolved_tab(
        self,
        tab_id: str,
        title: str,
        text: str,
        root_path: Path,
        line_sources: Sequence[Optional[LineSource]] = (),
        line_number: Optional[int] = None,
    ) -> None:
        """Открыть вкладку с результатом XML-резолва.

        ``line_sources`` — владельцы строк в том же формате, что у LTX.
        """

    def open_file_with_search(self, path: Path, search_text: str) -> None:
        """Открыть файл и перейти к первому вхождению ``search_text``."""

    # --- уведомления и запросы --------------------------------------------

    def log(self, message: str) -> None:
        """Записать сообщение в журнал."""

    def log_status(self, key: str, message: str) -> None:
        """Обновить одну статусную строку журнала по ключу."""

    def set_status(self, message: str, timeout: int = 0) -> None:
        """Показать сообщение в статусной строке."""

    def show_error(self, text: str) -> None:
        """Показать ошибку пользователю."""

    def ask_directory(self, initial: str = "") -> Optional[str]:
        """Запросить каталог у пользователя."""

    def ask_text(self, prompt: str, initial: str = "") -> str:
        """Запросить строку у пользователя."""

    def show_text(
        self,
        text: str,
        label: str,
        readonly: bool = True,
        source_path: Optional[Path] = None,
    ) -> None:
        """Показать текст в редакторе/просмотрщике платформы.

        ``source_path`` — файл, содержимое которого показано. Нужен, чтобы
        событие открытия можно было сопоставить с отложенной навигацией.
        """

    def show_module_window(self, key: str, window) -> None:
        """Показать окно, созданное модулем, и передать его платформе."""

    def close_module_window(self, key: str) -> None:
        """Закрыть окно модуля по ключу."""

    def open_editor_tab(self, tab_id: str, title: str, widget) -> None:
        """Открыть вкладку редактора, созданную модулем."""

    def close_editor_tab(self, tab_id: str) -> None:
        """Закрыть вкладку модуля по ключу."""

    def copy_to_clipboard(self, text: str) -> None:
        """Поместить текст в буфер обмена."""

    def theme_color(self, name: str, fallback: Optional[str] = None) -> Optional[str]:
        """Вернуть цвет темы по имени токена.

        Модуль не импортирует ``ui.theme`` и не знает структуру палитры:
        он запрашивает только разрешённый токен.
        """

    # --- команды платформы ------------------------------------------------

    def open_raw(self, path: Path) -> None:
        """Открыть файл в редакторе без обработчиков модуля."""

    def open_at_line(self, path: Path, line_number: int) -> None:
        """Открыть файл и перейти к строке."""

    def open_raw_at_line(self, path: Path, line_number: int) -> None:
        """Открыть исходный файл без резолва и перейти к строке."""

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
        """Запустить ``fn(log)`` в фоне.

        ``fn`` выполняется в рабочем потоке. ``on_done`` и ``on_log``
        вызываются в потоке UI — в них можно обращаться к виджетам.
        """


class EventApi(Protocol):
    """Шина событий, доступная модулю через ``ctx.events``.

    ``publish`` — уведомить о факте. Вызываются все подписчики.
    ``dispatch`` — разослать перехватываемое UI-действие. Первый обработчик,
    вернувший truthy, останавливает цепочку.

    ``publish`` можно вызывать из фонового потока: подписчики получат
    событие в потоке UI. ``dispatch`` предназначен для вызова из UI-потока.
    """

    def subscribe(self, name: str, handler: Callable, priority: int = 0):
        """Подписаться. Возвращает объект с методом ``unsubscribe()``."""

    def unsubscribe(self, name: str, handler: Optional[Callable] = None) -> int:
        """Отписаться от события по функции или от всех подписок события."""

    def publish(self, name: str, payload=None) -> None:
        """Опубликовать факт."""

    def dispatch(self, name: str, payload=None) -> bool:
        """Разослать перехватываемое действие."""

    def has(self, name: str) -> bool:
        """Есть ли подписчики у события."""


class CommandApi(Protocol):
    """Шина команд, доступная модулю через ``ctx.commands``."""

    def register(self, name: str, handler: Callable) -> None:
        """Зарегистрировать команду."""

    def unregister(self, name: str) -> bool:
        """Удалить команду."""

    def has(self, name: str) -> bool:
        """Зарегистрирована ли команда."""

    def execute(self, name: str, *args, **kwargs):
        """Выполнить команду."""

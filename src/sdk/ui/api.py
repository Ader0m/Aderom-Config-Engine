# -*- coding: utf-8 -*-
"""Протокол UI-платформы, доступный модулю через ``ctx.ui``."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Protocol

from sdk.contracts.forest import ForestRef
from sdk.contracts.models import ProjectOverlay, TreeReady
from sdk.kernel.registrations import RegistrationHandle
from sdk.ui.windows import DockSpec, TabSpec, WindowSpec


class UiApi(Protocol):
    """Меню, окна, вкладки, уведомления и тема."""

    def register_menu(
        self,
        menu_title: str,
        label: str,
        callback: Callable,
        shortcut: Optional[str] = None,
        action: Optional[str] = None,
    ) -> RegistrationHandle:
        """Добавить пункт меню.

        Если задан ``action``, нажатие сначала проходит через ``dispatch``:
        другой модуль может перехватить действие и не дать вызвать ``callback``.
        Возвращённый handle снимает пункт меню.
        """

    def register_tree_context_action(
        self,
        label: str,
        handler: Callable,
        predicate: Optional[Callable[[Path], bool]] = None,
    ) -> RegistrationHandle:
        """Добавить действие в контекстное меню дерева.

        ``predicate(path)`` — необязательный фильтр видимости действия
        для конкретного файла (например, только для ``.xml``).
        """

    def register_file_action(
        self,
        label: str,
        handler: Callable,
        predicate: Optional[Callable[[Path], bool]] = None,
    ) -> RegistrationHandle:
        """Добавить действие в контекстное меню вкладки файла.

        ``handler(path)`` вызывается платформой. ``predicate(path)``
        необязателен и решает, показывать ли действие для файла.
        """

    def register_open_handler(self, handler) -> RegistrationHandle:
        """Зарегистрировать обработчик открытия файлов.

        Обработчик принадлежит модулю и снимается вместе с его scope.
        """

    def register_shortcut(
        self,
        key: str,
        callback: Callable,
        scope: str = "global",
        action: Optional[str] = None,
    ) -> RegistrationHandle:
        """Зарегистрировать горячую клавишу.

        ``scope``: ``global``, ``editor`` или ``tree``.
        ``action`` позволяет перехватить нажатие через ``dispatch_action``.
        Без ``action`` всё равно рассылается общее действие
        :attr:`sdk.contracts.events.Events.UI_SHORTCUT`.
        """

    def register_mouse_binding(
        self,
        button,
        modifiers,
        callback: Callable,
        scope: str = "global",
        event: str = "press",
        action: Optional[str] = None,
    ) -> RegistrationHandle:
        """Зарегистрировать мышиную привязку.

        ``button``: ``left``/``right``/``middle`` или Qt-кнопка.
        ``modifiers``: ``Ctrl+Shift``, список или Qt-флаги.
        ``event``: ``press``/``release``/``double``/``move``.
        Callback получает QMouseEvent; truthy-результат поглощает событие.
        Без ``action`` всё равно рассылается общее действие
        :attr:`sdk.contracts.events.Events.UI_MOUSE`.
        """

    def register_action_handler(
        self,
        action: str,
        handler: Callable,
        priority: int = 0,
    ):
        """Подписаться на перехватываемое UI-действие.

        Это механизм замены реакции: обработчики вызываются от большего
        ``priority`` к меньшему, первый truthy-результат останавливает
        цепочку. Модуль может заменить реакцию платформы или другого
        модуля, зарегистрировав обработчик с более высоким приоритетом.
        Чужие подписки при этом не удаляются.
        """

    def dispatch_action(self, action: str, payload=None) -> bool:
        """Разослать UI-действие. Возвращает ``True``, если оно перехвачено."""

    def is_project_active(self) -> bool:
        """Есть ли активный проект (без него правка запрещена)."""

    def project_name(self) -> str:
        """Имя активного проекта или пустая строка."""

    def project_overlay(self) -> ProjectOverlay:
        """Единый слой правок активного проекта для VFS."""

    def refresh_workspace(self):
        """Пересобрать виртуальные файлы проекта в текущем лесе.

        Модуль не должен сам вызывать ``add_virtual_file``: платформа
        публикует новое поколение дерева и сбрасывает зависимые кэши.
        """

    def workspace_forest(self) -> Optional[ForestRef]:
        """Текущий лес как read-only handle.

        Модуль может читать безопасные проекции и передавать handle в
        ``ctx.services``, но не получает изменяемый ``LtxForest``.
        """

    def publish_tree_ready(self, forest: ForestRef) -> TreeReady:
        """Опубликовать новое поколение дерева.

        Платформа сохраняет лес у себя и рассылает лёгкий ``TreeReady``;
        модули читают его через ``workspace_forest()``.
        """

    def workspace_generation(self) -> int:
        """Номер поколения workspace (меняется при пересборке/VFS)."""

    def set_editor_tab_dirty(self, tab_id: str, dirty: bool) -> None:
        """Пометить вкладку модуля как изменённую/сохранённую."""

    def set_editor_tab_busy(self, tab_id: str, busy: bool) -> None:
        """Показать/снять «часики» сохранения на вкладке модуля."""

    def save_project_now(self) -> int:
        """Сохранить черновики проекта сейчас (аналог Ctrl+S)."""

    def open_file_with_search(self, path: Path, search_text: str) -> None:
        """Открыть файл и перейти к первому вхождению ``search_text``."""

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
        line_number: Optional[int] = None,
        syntax: bool = True,
        notice: Optional[str] = None,
        request_id: Optional[int] = None,
        tab_id: Optional[str] = None,
    ) -> None:
        """Показать текст в редакторе/просмотрщике платформы.

        ``source_path`` — файл, содержимое которого показано. Нужен, чтобы
        событие открытия можно было сопоставить с отложенной навигацией.
        ``line_number`` — перейти к строке после показа.
        ``request_id`` — id запроса открытия; ``None`` — взять активный
        запрос или ожидающий запрос для ``source_path``.
        ``tab_id`` — открыть/переиспользовать отдельную текстовую вкладку;
        ``None`` — зарезервированная вкладка ``ui.editor``.
        """

    def open_window(self, spec: WindowSpec, widget) -> bool:
        """Открыть окно модуля по описанию.

        Платформа сама выбирает контейнер по ``spec.kind``:
        ``dock``, ``floating``, ``dialog``, ``document_tab``/``tool_tab``.
        Владелец берётся из контекста модуля.
        """

    def open_tab(self, spec: TabSpec, widget) -> bool:
        """Открыть вкладку редактора по описанию.

        ``spec.owner`` обязателен; фактический владелец — текущий модуль.
        """

    def open_dock(self, spec: DockSpec, widget) -> bool:
        """Открыть док-панель модуля по описанию."""

    def save_state(self, state_key: str, state: dict) -> None:
        """Сохранить состояние содержимого, привязанное к ``state_key``."""

    def load_state(self, state_key: str) -> dict:
        """Прочитать сохранённое состояние содержимого."""

    def close_module_window(self, key: str) -> None:
        """Закрыть окно или док модуля по ключу."""

    def close_editor_tab(self, tab_id: str) -> None:
        """Закрыть вкладку модуля по ключу."""

    def copy_to_clipboard(self, text: str) -> None:
        """Поместить текст в буфер обмена."""

    def attach_text_hints(
        self,
        widget,
        target=None,
        providers=None,
        enabled: bool = True,
    ) -> HintsHandle:
        """Подключить движок подсказок к текстовому полю.

        ``widget`` — любой поддерживаемый платформой текстовый виджет
        (``QPlainTextEdit``/``QTextEdit``/``QLineEdit``/``QsciScintilla``).
        ``target`` — :class:`sdk.ui.hints.HintTargetInfo` или callable,
        возвращающий его; модуль описывает поверхность, но не знает о
        провайдерах и popup. ``providers`` — необязательное подмножество
        провайдеров (для специальных полей). Возвращает
        :class:`sdk.ui.hints.HintsHandle`.
        """

    def theme_color(self, name: str, fallback: Optional[str] = None) -> Optional[str]:
        """Вернуть цвет темы по имени токена.

        Модуль не импортирует ``ui.platform.theme`` и не знает структуру
        палитры: он запрашивает только разрешённый токен.
        """

    def request_window(self, window_id: str) -> bool:
        """Попросить модуль открыть своё окно по ключу."""

    def request_tab(self, tab_id: str) -> bool:
        """Попросить модуль открыть/сфокусировать вкладку по ключу."""

    def request_file_open(self, path: Path) -> bool:
        """Попросить открыть файл через перехватываемое действие.

        Если никто не перехватил запрос, платформа открывает файл сама.
        ``False`` означает, что открытие не удалось.
        """

    def current_file_request_id(self) -> int:
        """id текущего запроса открытия (``0`` вне обработчика).

        Асинхронный обработчик должен сохранить значение при старте и
        передать его в ``open_resolved_tab``/``open_xml_resolved_tab``/
        ``notify_file_open_failed``.
        """

    def open_file(self, path: Path) -> None:
        """Открыть файл через общий маршрутизатор обработчиков."""

    def open_raw(self, path: Path) -> None:
        """Открыть файл в редакторе без обработчиков модуля."""

    def notify_file_open_failed(
        self,
        path: Path,
        reason: str = "",
        request_id: Optional[int] = None,
    ) -> None:
        """Сообщить платформе, что открытие файла не состоялось.

        ``request_id`` нужен асинхронным обработчикам: без него платформа
        отменит самый старый ожидающий запрос для этого пути.
        """

    def open_at_line(self, path: Path, line_number: int) -> None:
        """Открыть файл и перейти к строке."""

    def open_raw_at_line(self, path: Path, line_number: int) -> None:
        """Открыть исходный файл без обработчиков и перейти к строке."""

    def undo(self) -> None:
        """Отменить действие в редакторе."""

    def redo(self) -> None:
        """Повторить действие в редакторе."""

    def find_in_editor(self) -> None:
        """Открыть поиск по текущему файлу."""

    def sort_file_tree_by_files_desc(self) -> None:
        """Отсортировать дерево по количеству файлов по убыванию."""

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

import hashlib
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
class ProjectOverlay:
    """Единый слой правок проекта поверх VFS.

    ``source_files`` — черновики реальных файлов ``{path: text}``.
    ``resolved_edits`` — правки resolved-корней:
    ``{root_path: (original_text, current_text, line_sources)}``.
    """

    source_files: dict = field(default_factory=dict)
    resolved_edits: dict = field(default_factory=dict)
    new_files: frozenset = field(default_factory=frozenset)

    @property
    def revision(self) -> str:
        digest = hashlib.sha1()

        for path, text in sorted(
            self.source_files.items(),
            key=lambda item: str(item[0]).lower(),
        ):
            digest.update(str(path).lower().encode("utf-8", "replace"))
            digest.update(b"\0")
            digest.update(
                hashlib.sha1(
                    str(text).encode("utf-8", "replace")
                ).digest()
            )
            digest.update(b"\1")

        for path, value in sorted(
            self.resolved_edits.items(),
            key=lambda item: str(item[0]).lower(),
        ):
            digest.update(str(path).lower().encode("utf-8", "replace"))
            digest.update(b"\2")
            digest.update(
                hashlib.sha1(
                    str(value[0]).encode("utf-8", "replace")
                ).digest()
            )
            digest.update(b"\0")
            digest.update(
                hashlib.sha1(
                    str(value[1]).encode("utf-8", "replace")
                ).digest()
            )
            digest.update(b"\1")

        return digest.hexdigest()

    def source_map(self) -> dict:
        return dict(self.source_files)

    def resolved_map(self) -> dict:
        return dict(self.resolved_edits)


@dataclass(frozen=True)
class AssetDraftInfo:
    """Двоичный черновик проекта (ресурс вне configs)."""

    path: Path
    is_new: bool = False
    changed: bool = False
    dirty: bool = False
    size: int = 0


@dataclass(frozen=True)
class FileView:
    """Представление файла для проекта (например, для предпросмотра).

    ``text`` — итоговый человекочитаемый вид. Для ``.ogg`` это
    комментарий движка; для текстовых форматов — обычный текст.
    ``label`` и ``details`` показываются в панели предпросмотра и не
    влияют на сам патч.
    """

    text: str
    label: str = ""
    details: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ProjectAssetType:
    """Тип двоичного ресурса, который модуль добавляет в проект.

    Пример для звуков::

        ProjectAssetType(
            key="sound.ogg",
            label="Звук Ogg Vorbis",
            extensions=(".ogg",),
            vfs_root="sounds",
        )

    Реестр типов принадлежит платформе. Проект использует его для
    проверки пути, предпросмотра и разрешения записи в патч-мод, поэтому
    ``core`` не знает про конкретные расширения.
    """

    key: str
    label: str
    extensions: tuple[str, ...]
    vfs_root: str


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

    @property
    def is_sound(self) -> bool:
        """Узел ссылается на звуковой файл (.ogg)."""
        return "sound_reference" in self.edge_types


@dataclass(frozen=True)
class SectionGraph:
    """Граф связей секции."""

    section_name: str
    root: GraphNode
    children: tuple[GraphNode, ...] = field(default_factory=tuple)
    # Заполняется только в режиме VFS: связи, отсутствующие в итоговом дереве.
    lost_children: tuple[GraphNode, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SectionGraphRequest:
    """Контекст для модулей, дополняющих граф связей секции."""

    forest: object
    section_name: str
    graph: SectionGraph
    vfs_mode: bool = False
    overlay: object = None


@dataclass(frozen=True)
class XmlResolveResult:
    """Результат XML-резолва (include-сборка + DXML)."""

    text: str
    virtual_name: str
    dxml_applied: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
    line_sources: tuple[LineSource, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ItemField:
    """Одно поле игрового предмета."""

    key: str
    value: str
    line_number: int = 0


@dataclass(frozen=True)
class ItemEntry:
    """Предмет из корневого файла предметов (``items.ltx``).

    ``icon_path`` — найденный в VFS атлас ``.dds`` (``None``, если
    текстура не найдена). ``icon_x``/``icon_y``/``icon_width``/
    ``icon_height`` — координаты и размер в ячейках инвентаря по 50 px.
    """

    section: str
    fields: tuple[ItemField, ...] = field(default_factory=tuple)
    icon_texture: str = ""
    icon_path: Optional[Path] = None
    icon_x: int = 0
    icon_y: int = 0
    icon_width: int = 1
    icon_height: int = 1
    line_number: int = 0
    # Готовый текст из configs/text/<lang>, а не ключ локализации.
    name_text: str = ""
    description_text: str = ""

    def field(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Значение поля без учёта регистра ключа."""
        wanted = str(name).lower()
        for item in self.fields:
            if item.key.lower() == wanted:
                return item.value
        return default

    @property
    def icon_rect(self) -> tuple[int, int, int, int]:
        """Прямоугольник иконки в пикселях атласа."""
        return (
            self.icon_x * 50,
            self.icon_y * 50,
            self.icon_width * 50,
            self.icon_height * 50,
        )


@dataclass(frozen=True)
class ItemSection:
    """Секция файла предметов: имя, поля и родители.

    ``fields`` — ключи, объявленные в секции; по ним создаётся новый
    предмет с пустыми значениями. ``parents`` — токены наследования
    из заголовка ``[section]:parent1,parent2``.
    """

    name: str
    fields: tuple[str, ...] = field(default_factory=tuple)
    parents: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ItemCatalog:
    """Отсортированный список предметов и файл, из которого он собран."""

    source_path: Optional[Path] = None
    items: tuple[ItemEntry, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    # Идентификатор снимка каталога для кэша иконок.
    cache_key: str = ""
    # Все секции документа, включая служебные родительские секции.
    sections: tuple[ItemSection, ...] = field(default_factory=tuple)
    # Родители, встречающиеся в заголовках исходных LTX.
    parent_names: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ItemIcon:
    """Декодированная иконка предмета в формате RGBA."""

    section: str
    width: int
    height: int
    rgba: bytes = b""


@dataclass(frozen=True)
class TextureRegion:
    """Прямоугольник DDS в формате RGBA (4 байта на пиксель)."""

    width: int
    height: int
    rgba: bytes = b""


@dataclass(frozen=True)
class ResolvedDraftInfo:
    """Черновик resolved-файла активного проекта."""

    text: str
    saved_text: str = ""
    original_text: str = ""
    line_sources: tuple = field(default_factory=tuple)

    @property
    def is_dirty(self) -> bool:
        return self.text != self.saved_text


@dataclass(frozen=True)
class ResolvedLineEdit:
    """Точечная правка одной строки resolved-файла."""

    action: str          # replace | insert | delete
    line_number: int
    text: str = ""


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
        overlay=None,
    ):
        """Построить дерево и вернуть непрозрачный handle для SDK.

        ``overlay`` — карта ``{path: text}`` с черновиками проекта.
        """

    def tree_summary(self, forest) -> TreeSummary:
        """Вернуть сводку по построенному дереву."""

    def relative_path(self, forest, path: Path) -> str:
        """Относительный путь файла в дереве."""

    def forest_scan_dir(self, forest) -> Optional[Path]:
        """Каталог, который сканировало дерево (``gamedata/configs``)."""

    def forest_system_root(self, forest) -> Optional[Path]:
        """Корневой ``system.ltx`` построенного дерева."""

    def game_root(self, forest) -> Optional[Path]:
        """Корень ``gamedata`` VFS, где лежат ``sounds``/``textures``/``meshes``."""

    def add_virtual_file(self, forest, path: Path, text: str) -> bool:
        """Добавить новый черновик в уже построенное дерево VFS."""

    def patch_target(self, forest, path: Path) -> Optional[Path]:
        """Базовый файл, к которому применяется DLTX-патч."""

    def read_text(self, path: Path) -> str:
        """Прочитать текстовый файл средствами ядра."""

    # --- фоновая предсборка ----------------------------------------------

    def start_prebuild(self, forest, config_dir: Path, overlay=None) -> None:
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
        overlay: Optional[ProjectOverlay] = None,
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
        overlay: Optional[ProjectOverlay] = None,
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

    # --- предметы и иконки -----------------------------------------------

    def item_catalog_root(self, forest) -> Optional[Path]:
        """Корень каталога предметов: ``system.ltx`` или fallback-файл."""

    def build_item_catalog(
        self,
        forest,
        log: Optional[Callable[[str], None]] = None,
        resolved_text: Optional[str] = None,
        reuse_snapshot_cache: bool = False,
    ) -> ItemCatalog:
        """Собрать каталог предметов из резолва ``system.ltx``.

        Основной корень — ``system.ltx`` (учитывает ``mod_system_*.ltx``);
        для урезанных VFS возможен fallback на ``items.ltx``/
        ``item_base.ltx``. Результат отсортирован по имени секции.

        ``resolved_text`` — готовый текст (черновик проекта); тогда резолв
        не выполняется, а кэш ключуется хэшем текста.
        """

    def read_texture_region(
        self,
        path: Path,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> TextureRegion:
        """Декодировать прямоугольник DDS-текстуры в RGBA.

        Используется для вырезания иконки из атласа по ``inv_grid_*``.
        Поддерживаются несжатые DDS и DXT1/DXT3/DXT5.
        """

    def load_item_icons(
        self,
        catalog: ItemCatalog,
        log: Optional[Callable[[str], None]] = None,
    ) -> tuple[ItemIcon, ...]:
        """Пакетно декодировать иконки каталога.

        Ядро использует кэш регионов и пул процессов; модуль получает
        готовые RGBA-иконки и не занимает UI-поток декодированием DDS.
        """

    # --- граф связей и внешние инструменты -------------------------------

    def build_section_graph(
        self,
        forest,
        section_name: str,
        vfs_mode: bool = False,
        log: Optional[Callable[[str], None]] = None,
        overlay: Optional[ProjectOverlay] = None,
    ) -> SectionGraph:
        """Построить граф связей для секции.

        ``vfs_mode=True`` — обойти всю VFS, не ограничиваясь LtxForest,
        и сравнить её с сырым итоговым деревом без запуска резолвера.
        В ``SectionGraph.lost_children`` попадут связи, которых нет в
        итоговом дереве.
        """

    def register_section_graph_contributor(
        self,
        contributor: Callable[[SectionGraphRequest], Sequence[GraphNode]],
    ) -> None:
        """Добавить поставщика дополнительных узлов графа.

        Модуль может зарегистрировать функцию, которая получает
        :class:`SectionGraphRequest` и возвращает узлы своего типа
        (например, .ogg-файлы для ``snd_*``-полей).
        """

    def unregister_section_graph_contributor(self, contributor) -> bool:
        """Удалить ранее зарегистрированного поставщика узлов."""

    def collect_section_graph_nodes(
        self,
        request: SectionGraphRequest,
        log: Optional[Callable[[str], None]] = None,
    ) -> tuple[GraphNode, ...]:
        """Собрать дополнительные узлы у всех зарегистрированных модулей."""

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

    def is_project_active(self) -> bool:
        """Есть ли активный проект (без него правка запрещена)."""

    def project_name(self) -> str:
        """Имя активного проекта или пустая строка."""

    def project_overlay(self) -> ProjectOverlay:
        """Единый слой правок активного проекта для VFS."""

    def read_project_text(self, path: Path) -> str:
        """Текст файла из черновика проекта (или с диска, если черновика нет)."""

    def write_project_text(self, path: Path, text: str) -> bool:
        """Записать текст в черновик проекта, не меняя оригинальный файл."""

    def register_project_asset_type(self, asset_type: ProjectAssetType) -> bool:
        """Зарегистрировать тип двоичного ресурса проекта.

        Регистрация действует до выгрузки модуля. Конфликт ключа или пары
        ``(vfs_root, extension)`` не перезаписывает существующий тип.
        """

    def unregister_project_asset_type(self, key: str) -> bool:
        """Убрать тип ресурса, зарегистрированный модулем."""

    def project_asset_types(self) -> tuple[ProjectAssetType, ...]:
        """Все зарегистрированные типы двоичных ресурсов."""

    def register_file_reader(self, reader):
        """Зарегистрировать читатель файлов.

        Возвращает объект с методом ``unsubscribe()``. Читатель
        вызывается в фоновом потоке предпросмотра проекта, поэтому его
        ``read`` должен быть чистым и потокобезопасным.
        """

    def read_file_view(
        self,
        path: Path,
        data: Optional[bytes] = None,
    ) -> Optional[FileView]:
        """Получить представление файла от первого подходящего читателя."""

    def read_project_asset(self, path: Path) -> Optional[bytes]:
        """Текущие байты двоичного черновика или ``None``."""

    def update_project_asset(self, path: Path, data: bytes) -> bool:
        """Обновить двоичный черновик в памяти (без записи в проект)."""

    def save_project_asset(
        self,
        path: Path,
        data: Optional[bytes] = None,
    ) -> bool:
        """Записать двоичный черновик в каталог проекта сейчас."""

    def project_asset_drafts(self) -> tuple[AssetDraftInfo, ...]:
        """Двоичные черновики активного проекта."""

    def set_editor_tab_dirty(self, tab_id: str, dirty: bool) -> None:
        """Пометить вкладку модуля как изменённую/сохранённую."""

    def resolved_draft(self, tab_id: str) -> Optional[ResolvedDraftInfo]:
        """Текущий resolved-черновик проекта по id вкладки."""

    def update_resolved_draft(
        self,
        tab_id: str,
        root_path: Path,
        title: str,
        original_text: str,
        current_text: str,
        line_sources: Sequence[Optional[LineSource]] = (),
        line_edit: Optional[ResolvedLineEdit] = None,
    ) -> bool:
        """Обновить resolved-черновик проекта (без записи в оригинал).

        ``line_edit`` позволяет обновить уже открытую resolved-вкладку
        точечно, не заменяя весь текст (он может быть в десятки мегабайт).
        """

    def sync_resolved_tab(
        self,
        tab_id: str,
        text: str,
        line_sources: Sequence[Optional[LineSource]],
        root_path: Path,
    ) -> None:
        """Обновить уже открытую resolved-вкладку, не переключая фокус."""

    def save_project_now(self) -> int:
        """Сохранить черновики проекта сейчас (аналог Ctrl+S)."""

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

    def open_file(self, path: Path) -> None:
        """Открыть файл через общий маршрутизатор обработчиков."""

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

# -*- coding: utf-8 -*-
"""Контракт сервисов ядра, доступный модулю через ``ctx.services``."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence

from sdk.contracts.forest import ForestRef
from sdk.contracts.models import (
    ProjectOverlay,
    SectionGraph,
    SectionGraphContribution,
    SectionGraphRequest,
    TreeSummary,
)
from sdk.features.documents.models import LineSource, XmlResolveResult
from sdk.features.patches.models import PatchTargetInfo
from sdk.kernel.registrations import RegistrationHandle


class CoreApi(Protocol):
    """Дерево, резолвер, граф и внешние инструменты."""

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
    ) -> ForestRef:
        """Построить дерево и вернуть непрозрачный handle для SDK.

        ``overlay`` — карта ``{path: text}`` с черновиками проекта.
        """

    def tree_summary(self, forest: ForestRef) -> TreeSummary:
        """Вернуть сводку по построенному дереву."""

    def relative_path(self, forest: ForestRef, path: Path) -> str:
        """Относительный путь файла в дереве."""

    def forest_scan_dir(self, forest: ForestRef) -> Optional[Path]:
        """Каталог, который сканировало дерево (``gamedata/configs``)."""

    def forest_system_root(self, forest: ForestRef) -> Optional[Path]:
        """Корневой ``system.ltx`` построенного дерева."""

    def game_root(self, forest: ForestRef) -> Optional[Path]:
        """Корень ``gamedata`` VFS, где лежат ``sounds``/``textures``/``meshes``."""

    def add_virtual_file(self, forest: ForestRef, path: Path, text: str) -> bool:
        """Добавить новый черновик в уже построенное дерево VFS."""

    def patch_target_info(
        self,
        forest: ForestRef,
        path: Path,
    ) -> PatchTargetInfo:
        """Полная информация о цели патча: корень и кандидаты."""

    def patch_target(self, forest: ForestRef, path: Path) -> Optional[Path]:
        """Базовый файл, к которому применяется DLTX-патч.

        ``None`` означает, что корень не найден или неоднозначен;
        подробности — в :meth:`patch_target_info`.
        """

    def read_text(self, path: Path) -> str:
        """Прочитать текстовый файл средствами ядра."""

    def read_xml_text(self, path: Path) -> str:
        """Прочитать XML-файл по правилам X-Ray (windows-1251)."""

    def color_definitions(
        self,
        forest: ForestRef,
        overlay=None,
        config_dir: Optional[Path] = None,
    ) -> dict:
        """Цвета ``%c[name]`` из ``configs/ui/color_defs.xml``.

        Возвращает ``{name: (r, g, b, a)}`` с учётом overlay проекта.
        Модуль использует это для отрисовки текста движка; подсказка
        берёт те же данные из общего каталога ядра.
        """

    def ltx_section_at_line(self, text: str, line_number: int):
        """Имя секции на строке (1-based) или ``None``."""

    def ltx_section_bounds(self, text: str, section: str):
        """Границы секции ``(start, end)`` в 1-based нумерации."""

    def ltx_field_line(self, text: str, section: str, key: str) -> int:
        """Номер строки поля (последнее set-вхождение) или 0."""

    def ltx_key_from_line(self, line: str) -> str:
        """Ключ строки LTX без DLTX-префикса и значения."""

    def ltx_declared_sections(self, text: str):
        """Все имена секций в порядке появления."""

    def ltx_edit_section(
        self,
        text: str,
        section: str,
        key: str,
        action: str,
        new_line: Optional[str] = None,
        after_key: Optional[str] = None,
    ):
        """Точечно изменить строку секции, сохранив переводы строк."""

    def start_prebuild(self, forest: ForestRef, config_dir: Path, overlay=None) -> None:
        """Запустить фоновую сборку всех LTX-файлов дерева."""

    def ensure_prebuild(self, forest: Optional[ForestRef] = None, config_dir=None, overlay=None) -> bool:
        """Запустить/перезапустить сборку, если кеш устарел.

        Возвращает True, если сборка была запущена.
        """

    def prebuild_status(self) -> dict:
        """Текущий статус фоновой сборки: state/done/total/error."""

    def wait_prebuild(
        self,
        log: Optional[Callable[[str], None]] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Дождаться окончания фоновой сборки (с необязательным timeout)."""

    def stop_prebuild(self) -> None:
        """Остановить фоновую сборку."""

    def run_resolver(
        self,
        forest: ForestRef,
        config_dir: Path,
        log: Optional[Callable[[str], None]] = None,
    ):
        """Выполнить проход резолвера."""

    def assemble_text(
        self,
        root_path: Path,
        forest: ForestRef,
        config_dir: Path,
        log: Optional[Callable[[str], None]] = None,
        overlay: Optional[ProjectOverlay] = None,
    ) -> str:
        """Собрать итоговый текст файла так, как его видит игра."""

    def assemble_to_file(self, root_path: Path, forest: ForestRef) -> Path:
        """Собрать файл в каталог сгенерированных конфигов."""

    def assemble_resolved(
        self,
        root_path: Path,
        forest: ForestRef,
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
        overlay: Optional[ProjectOverlay] = None,
    ) -> XmlResolveResult:
        """Разрешить XML: #include-сборка + DXML-патчи.

        ``overlay`` учитывает XML-черновики проекта так же, как LTX-резолв.
        """

    def resolve_xml_to_file(
        self,
        path: Path,
        config_dir: Path,
        log: Optional[Callable[[str], None]] = None,
        dxml: bool = True,
        overlay: Optional[ProjectOverlay] = None,
    ) -> Path:
        """Разрешить XML и сохранить результат в generated/xml/."""

    def build_section_graph(
        self,
        forest: ForestRef,
        section_name: str,
        vfs_mode: bool = False,
        log: Optional[Callable[[str], None]] = None,
        overlay: Optional[ProjectOverlay] = None,
    ) -> SectionGraph:
        """Построить граф связей для секции.

        ``vfs_mode=True`` — обойти всю VFS, не ограничиваясь LtxForest,
        без запуска резолвера. Ядро возвращает один список ``children``;
        разделение на живые и потерянные делает вызывающая сторона,
        сравнивая его с уже построенным resolved-графом.
        """

    def register_section_graph_contributor(
        self,
        contributor: Callable[[SectionGraphRequest], Sequence[GraphNode]],
    ) -> RegistrationHandle:
        """Добавить поставщика дополнительных узлов графа.

        Модуль может зарегистрировать функцию, которая получает
        :class:`SectionGraphRequest` и возвращает узлы своего типа
        (например, .ogg-файлы для ``snd_*``-полей). Возвращает handle,
        привязанный к модулю: снять можно только свою регистрацию.
        """

    def unregister_section_graph_contributor(self, contributor) -> bool:
        """Удалить ранее зарегистрированного поставщика узлов.

        Снимается только регистрация текущего модуля; чужой contributor
        удалить нельзя.
        """

    def collect_section_graph_nodes(
        self,
        request: SectionGraphRequest,
        log: Optional[Callable[[str], None]] = None,
    ) -> tuple[GraphNode, ...]:
        """Собрать дополнительные узлы у всех зарегистрированных модулей."""

    def collect_section_graph_contribution(
        self,
        request: SectionGraphRequest,
        log: Optional[Callable[[str], None]] = None,
    ) -> SectionGraphContribution:
        """Собрать дополнительные связи и их оформление у модулей."""

    def open_external_editor(self, path: Path) -> tuple[bool, str]:
        """Открыть файл во внешнем редакторе. Возвращает ``(ok, message)``."""

    def open_with_shell(self, path: Path) -> tuple[bool, str]:
        """Открыть файл приложением Windows по умолчанию.

        Если ассоциация не настроена, показывается системный диалог
        «Открыть с помощью…». Возвращает ``(ok, message)``.
        """

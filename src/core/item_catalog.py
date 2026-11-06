# -*- coding: utf-8 -*-
"""Каталог игровых предметов из корневого файла ``items.ltx``.

Модуль решает одну задачу: по уже построенному дереву VFS прочитать
корневой файл предметов так, как его видит игра (include + DLTX +
наследование через Resolver4), и вернуть плоский список секций с их
полями и координатами иконки в атласе.

Правила движка, которые здесь воспроизводятся:

- ``icons_texture`` по умолчанию ``ui\\ui_icon_equipment``;
- ``inv_grid_x``/``inv_grid_y`` по умолчанию 0;
- ``inv_grid_width``/``inv_grid_height`` по умолчанию 1;
- одна ячейка инвентаря — 50×50 пикселей.

Служебные секции (``tch_*``, ``mp_*``, ``*_base``) в список не попадают,
как и в debug-меню игры.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .documents.document_model import parse_ltx_document
from .fs import read_text_safe
from .object_cache import ObjectCache
from .resolved_cache import file_subtree_signature, path_key
from .vfs_paths import resolve_asset_path


# Меняется при изменении правил разбора: старый кэш станет недоступен.
ITEM_CATALOG_VERSION = 1

DEFAULT_ICON_TEXTURE = r"ui\ui_icon_equipment"
GRID_SIZE = 50

GRID_KEYS = (
    "inv_grid_x",
    "inv_grid_y",
    "inv_grid_width",
    "inv_grid_height",
)

# Корневые файлы предметов. В Anomaly корень — item_base.ltx, но
# пользовательские сборки часто кладут объединённый items.ltx.
ROOT_NAMES = ("items.ltx", "item.ltx", "item_base.ltx")

# Приоритет путей: сначала ожидаемые места, затем любой файл с таким именем.
ROOT_PATH_RANK = {
    "items/items.ltx": 0,
    "items/item.ltx": 1,
    "items/item_base.ltx": 2,
    "items.ltx": 3,
    "item.ltx": 4,
    "item_base.ltx": 5,
}

_EXCLUDED_PATH_PARTS = ("/settings/", "/trade/", "/plugins/")


@dataclass(frozen=True)
class ItemField:
    """Одно поле предмета (ключ и его итоговое значение)."""

    key: str
    value: str


@dataclass(frozen=True)
class ItemRecord:
    """Предмет из корневого файла предметов."""

    section: str
    fields: Tuple[ItemField, ...]
    icon_texture: str
    icon_path: Optional[Path]
    icon_x: int
    icon_y: int
    icon_width: int
    icon_height: int
    line_number: int = 0

    def field(self, name: str, default: Optional[str] = None) -> Optional[str]:
        wanted = str(name).lower()
        for item in self.fields:
            if item.key.lower() == wanted:
                return item.value
        return default

    @property
    def icon_rect(self) -> Tuple[int, int, int, int]:
        """Прямоугольник иконки в пикселях атласа."""
        return (
            self.icon_x * GRID_SIZE,
            self.icon_y * GRID_SIZE,
            self.icon_width * GRID_SIZE,
            self.icon_height * GRID_SIZE,
        )


@dataclass(frozen=True)
class ItemCatalog:
    """Результат разбора корневого файла предметов."""

    source_path: Optional[Path]
    items: Tuple[ItemRecord, ...] = ()
    warnings: Tuple[str, ...] = ()
    # Идентификатор снимка каталога: используется кэшем иконок.
    cache_key: str = ""


class ItemCatalogService:
    """Строит каталог предметов через сервис резолвера."""

    def __init__(self, resolver_service, cache_dir=None, max_entries=4):
        self._resolver = resolver_service
        self._cache = ObjectCache(
            cache_dir=cache_dir,
            max_entries=max_entries,
        )

    # -------------------------------------------------- корневой файл

    def find_root(self, forest) -> Optional[Path]:
        """Найти корневой файл предметов в дереве VFS."""
        files = list(getattr(forest, "files", ()) or ())
        if not files:
            return None

        rel_paths = getattr(forest, "rel_paths", {}) or {}
        scan_dir = getattr(forest, "scan_dir", None)
        ranked = []

        for path in files:
            path = Path(path)
            if path.name.lower() not in ROOT_NAMES:
                continue

            rel = str(rel_paths.get(path) or "")
            if not rel and scan_dir:
                try:
                    rel = str(path.relative_to(Path(scan_dir)))
                except ValueError:
                    rel = ""
            if not rel:
                rel = path.name

            rel = rel.replace("\\", "/").lower().lstrip("./")

            if any(part in ("/" + rel) for part in _EXCLUDED_PATH_PARTS):
                continue

            rank = ROOT_PATH_RANK.get(rel)
            if rank is None:
                rank = 4

            ranked.append((rank, len(rel), rel, path))

        if not ranked:
            return None

        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        return ranked[0][3]

    # -------------------------------------------------- кэш снимка

    def _snapshot_key(self, forest, root):
        """Ключ кэша: версия правил + путь + подпись include/DLTX-дерева."""
        try:
            signature = file_subtree_signature(forest, root)
        except Exception:
            signature = "unknown"

        raw = "%s|%s|%s" % (
            ITEM_CATALOG_VERSION,
            path_key(root),
            signature,
        )
        digest = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()
        return ("item_catalog", digest), digest

    def clear_memory(self):
        """Сбросить каталоги в памяти (диск остаётся до смены снимка)."""
        self._cache.clear()

    # -------------------------------------------------- сборка

    def build(self, forest, log=None, config_dir=None) -> ItemCatalog:
        """Прочитать корень предметов и вернуть отсортированный каталог."""
        root = self.find_root(forest)

        if root is None:
            return ItemCatalog(
                source_path=None,
                items=(),
                warnings=(
                    "В VFS не найден файл items.ltx, item.ltx или item_base.ltx.",
                ),
            )

        cache_key, catalog_key = self._snapshot_key(forest, root)
        cached = self._cache.get(cache_key)
        if cached is not None:
            if log:
                log("Предметы: кэш снимка %s" % root.name)
            return cached

        text = None
        if log:
            log("Предметы: корневой файл %s" % root)

        try:
            cfg = config_dir or getattr(forest, "scan_dir", None)
            text, _sources = self._resolver.assemble_resolved(
                root,
                forest,
                cfg,
                log=log,
            )
        except Exception as exc:
            if log:
                log(
                    "Предметы: резолв недоступен (%s), читаю исходный файл"
                    % exc
                )

        if not text:
            try:
                text = read_text_safe(root)
            except OSError as exc:
                return ItemCatalog(
                    source_path=root,
                    items=(),
                    warnings=(
                        "Не удалось прочитать %s: %s" % (root, exc),
                    ),
                )

        document = parse_ltx_document(text, source_path=root)
        items = self._collect(document, forest)

        if log:
            log(
                "Предметы: секций %d, в каталог попало %d"
                % (len(document.sections), len(items))
            )

        warnings = []
        if not items:
            warnings.append(
                "В %s не найдено секций с иконками или сеткой инвентаря."
                % root.name
            )

        catalog = ItemCatalog(
            source_path=root,
            items=tuple(items),
            warnings=tuple(warnings),
            cache_key=catalog_key,
        )
        self._cache.set(cache_key, catalog)
        return catalog

    # -------------------------------------------------- предметы

    def _collect(self, document, forest) -> list:
        scan_dir = getattr(forest, "scan_dir", None)
        items = []

        for name, section in document.sections.items():
            if self._is_service_section(name):
                continue

            values = section.view().present
            lower = {str(key).lower(): value for key, value in values.items()}

            if not self._looks_like_item(lower):
                continue

            icon_texture = self._clean(
                lower.get("icons_texture") or DEFAULT_ICON_TEXTURE
            )
            if not icon_texture:
                icon_texture = DEFAULT_ICON_TEXTURE

            grid_x = self._to_int(lower.get("inv_grid_x"), 0)
            grid_y = self._to_int(lower.get("inv_grid_y"), 0)
            grid_w = self._to_int(lower.get("inv_grid_width"), 1)
            grid_h = self._to_int(lower.get("inv_grid_height"), 1)

            if grid_w <= 0:
                grid_w = 1
            if grid_h <= 0:
                grid_h = 1

            fields = [
                ItemField(
                    key=str(key),
                    value="" if value is None else str(value),
                )
                for key, value in values.items()
            ]

            # Показываем эффективные значения даже там, где ключ не задан
            # явно: именно их использует игра (значения по умолчанию).
            self._set_field(fields, "icons_texture", icon_texture)
            self._set_field(fields, "inv_grid_x", str(grid_x))
            self._set_field(fields, "inv_grid_y", str(grid_y))
            self._set_field(fields, "inv_grid_width", str(grid_w))
            self._set_field(fields, "inv_grid_height", str(grid_h))

            icon_path = resolve_asset_path(
                scan_dir,
                "texture",
                icon_texture,
            )

            items.append(
                ItemRecord(
                    section=name,
                    fields=tuple(fields),
                    icon_texture=icon_texture,
                    icon_path=icon_path,
                    icon_x=grid_x,
                    icon_y=grid_y,
                    icon_width=grid_w,
                    icon_height=grid_h,
                    line_number=int(getattr(section, "line_number", 0) or 0),
                )
            )

        items.sort(key=lambda item: item.section.lower())
        return items

    @staticmethod
    def _is_service_section(name: str) -> bool:
        lowered = str(name).lower()
        if lowered.startswith("tch_") or lowered.startswith("mp_"):
            return True
        return "_base" in lowered

    @staticmethod
    def _looks_like_item(lower_fields) -> bool:
        if "icons_texture" in lower_fields:
            return True
        if any(key in lower_fields for key in GRID_KEYS):
            return True
        # Предмет без сетки/иконки всё равно остаётся предметом: окно
        # покажет для него заглушку 50×50.
        return bool(lower_fields.get("inv_name"))

    @staticmethod
    def _clean(value) -> str:
        return str(value or "").strip().strip('"').strip()

    @staticmethod
    def _to_int(value, default: int) -> int:
        text = str(value if value is not None else "").strip().strip('"')
        if not text:
            return default
        try:
            return int(round(float(text)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _set_field(fields: list, key: str, value: str) -> None:
        """Записать эффективное значение, сохранив исходный регистр ключа."""
        for index, field in enumerate(fields):
            if field.key.lower() == key:
                fields[index] = ItemField(key=field.key, value=value)
                return
        fields.append(ItemField(key=key, value=value))

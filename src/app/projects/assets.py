# -*- coding: utf-8 -*-
"""Реестр типов двоичных ресурсов проекта.

Модули не должны заставлять `core` знать про `.ogg`, `.dds` или другие
конкретные расширения. Вместо этого модуль регистрирует тип ресурса
через SDK, а проект использует реестр для:

- проверки пути ``gamedata/<vfs_root>/...``;
- определения подписи и каталога патча;
- выдачи правил безопасности в ``PatchWriter``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from sdk.api import ProjectAssetType


class ProjectAssetTypeError(ValueError):
    """Тип ресурса нельзя зарегистрировать или применить."""


class ProjectAssetRegistry:
    """Набор типов ресурсов, зарегистрированных модулями."""

    def __init__(self, types: Iterable[ProjectAssetType] = ()):
        self._types: Dict[str, ProjectAssetType] = {}
        for asset_type in types:
            self.register(asset_type)

    # ------------------------------------------------------------ регистрация
    def register(
        self,
        asset_type: ProjectAssetType,
        replace: bool = False,
    ) -> ProjectAssetType:
        key = str(getattr(asset_type, "key", "") or "").strip()
        if not key:
            raise ProjectAssetTypeError("Ключ типа ресурса не может быть пустым")

        root = str(getattr(asset_type, "vfs_root", "") or "").strip()
        root = root.replace("\\", "/").strip("/")
        if not root or "/" in root or root in (".", ".."):
            raise ProjectAssetTypeError(
                "vfs_root должен быть одним каталогом внутри gamedata: %r"
                % root
            )
        if root.lower() in ("configs", "scripts"):
            raise ProjectAssetTypeError(
                "Каталог %s обслуживается собственным конвейером проекта"
                % root
            )

        raw_extensions = getattr(asset_type, "extensions", ()) or ()
        if isinstance(raw_extensions, str):
            raw_extensions = (raw_extensions,)

        extensions = []
        for value in raw_extensions:
            suffix = str(value or "").strip().lower()
            if not suffix:
                continue
            if not suffix.startswith("."):
                suffix = "." + suffix
            if "/" in suffix or "\\" in suffix or suffix in (".", ".."):
                raise ProjectAssetTypeError(
                    "Недопустимое расширение: %r" % suffix
                )
            if suffix not in extensions:
                extensions.append(suffix)

        if not extensions:
            raise ProjectAssetTypeError(
                "Тип ресурса %s не содержит расширений" % key
            )

        existing = self._types.get(key)
        if existing is not None and not replace:
            raise ProjectAssetTypeError(
                "Тип ресурса уже зарегистрирован: %s" % key
            )

        for registered in self._types.values():
            if registered.key == key:
                continue
            if (
                registered.vfs_root.lower() == root.lower()
                and set(registered.extensions) & set(extensions)
            ):
                raise ProjectAssetTypeError(
                    "Конфликт типа %s с %s: %s/%s"
                    % (
                        key,
                        registered.key,
                        root,
                        ", ".join(sorted(
                            set(registered.extensions) & set(extensions)
                        )),
                    )
                )

        normalized = ProjectAssetType(
            key=key,
            label=str(getattr(asset_type, "label", "") or key),
            extensions=tuple(extensions),
            vfs_root=root.lower(),
        )
        self._types[key] = normalized
        return normalized

    def unregister(self, key: str) -> bool:
        return self._types.pop(str(key or "").strip(), None) is not None

    def types(self) -> Tuple[ProjectAssetType, ...]:
        return tuple(
            sorted(self._types.values(), key=lambda item: item.key.lower())
        )

    def __len__(self) -> int:
        return len(self._types)

    # ------------------------------------------------------------ применение
    def find(self, relative_path) -> Optional[ProjectAssetType]:
        """Тип для пути относительно ``gamedata``."""
        relative = Path(relative_path)
        parts = relative.parts
        if len(parts) < 2:
            return None

        root = str(parts[0]).lower()
        suffix = relative.suffix.lower()
        for asset_type in self._types.values():
            if (
                asset_type.vfs_root.lower() == root
                and suffix in asset_type.extensions
            ):
                return asset_type
        return None

    def relative_for_path(
        self,
        game_root,
        path,
    ) -> Tuple[Path, ProjectAssetType]:
        """Проверить путь и вернуть ``(путь от gamedata, тип)``."""
        try:
            relative = Path(
                os.path.relpath(str(path), str(game_root))
            )
        except ValueError:
            raise ProjectAssetTypeError(
                "Ресурс находится вне gamedata: %s" % path
            )

        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
        ):
            raise ProjectAssetTypeError(
                "Ресурс находится вне gamedata: %s" % path
            )

        asset_type = self.find(relative)
        if asset_type is None:
            raise ProjectAssetTypeError(
                "Тип ресурса не зарегистрирован: %s" % relative
            )
        return relative, asset_type

    def patch_rules(self) -> Dict[str, Tuple[str, ...]]:
        """Правила для core: ``{vfs_root: (расширения, ...)}``."""
        rules: Dict[str, set] = {}
        for asset_type in self._types.values():
            rules.setdefault(asset_type.vfs_root, set()).update(
                asset_type.extensions
            )
        return {
            root: tuple(sorted(suffixes))
            for root, suffixes in rules.items()
        }

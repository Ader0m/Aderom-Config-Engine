# -*- coding: utf-8 -*-
"""Безопасная запись сгенерированного патча в отдельный патч-мод.

Писатель не имеет права трогать оригинальные файлы, VFS и Overwrite.
Он принимает только проверенный патч и только каталог отдельного мода.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import List, Sequence, Tuple

from .fs import normalize_path
from .patch_models import GeneratedPatch
from .patch_targets import validate_patch_mod_root


class PatchWriteError(Exception):
    """Патч нельзя безопасно записать."""


class PatchWriter:
    """Запись патч-файлов в отдельный каталог мода."""

    def __init__(
        self,
        require_verification: bool = True,
        require_resolver: bool = False,
    ):
        self.require_verification = bool(require_verification)
        self.require_resolver = bool(require_resolver)

    def _validate(
        self,
        generated: GeneratedPatch,
        patch_mod_root,
        forbidden_roots: Sequence[Path],
    ) -> Path:
        if not generated.files:
            raise PatchWriteError("Нет файлов для записи")

        if generated.plan.needs_confirmation and not generated.plan.confirmed:
            raise PatchWriteError(
                "План содержит опасные изменения; требуется подтверждение"
            )

        if self.require_verification:
            if generated.verification is None:
                raise PatchWriteError(
                    "Патч не проверен: запись запрещена"
                )
            if not generated.verification.ok:
                raise PatchWriteError(
                    "Проверка патча не пройдена: запись запрещена"
                )

            if self.require_resolver:
                resolver_checks = [
                    check
                    for check in generated.verification.checks
                    if check.name == "resolver"
                ]
                if not resolver_checks or not any(
                    check.ok for check in resolver_checks
                ):
                    raise PatchWriteError(
                        "Патч не проверен настоящим Resolver4: запись запрещена"
                    )

        root = normalize_path(patch_mod_root)
        ok, message = validate_patch_mod_root(root, forbidden_roots)
        if not ok:
            raise PatchWriteError(message)

        for file in generated.files:
            rel = Path(file.relative_path)

            if rel.is_absolute() or ".." in rel.parts:
                raise PatchWriteError(
                    "Небезопасный относительный путь: %s" % rel
                )

            parts = [part.lower() for part in rel.parts]
            if "overwrite" in parts:
                raise PatchWriteError("Запись в Overwrite запрещена")

            if tuple(parts[:2]) != ("gamedata", "configs"):
                raise PatchWriteError(
                    "Патч должен лежать в gamedata/configs: %s" % rel
                )

        return root

    def plan_paths(
        self,
        generated: GeneratedPatch,
        patch_mod_root,
        forbidden_roots: Sequence[Path] = (),
    ) -> Tuple[Path, ...]:
        """Вернуть итоговые пути, ничего не записывая."""
        root = self._validate(
            generated,
            patch_mod_root,
            forbidden_roots,
        )
        return tuple(root / Path(file.relative_path) for file in generated.files)

    def write(
        self,
        generated: GeneratedPatch,
        patch_mod_root,
        forbidden_roots: Sequence[Path] = (),
        overwrite: bool = False,
    ) -> Tuple[Path, ...]:
        """Записать патч в отдельный мод. Возвращает записанные пути."""
        root = self._validate(
            generated,
            patch_mod_root,
            forbidden_roots,
        )

        prepared = []
        written: List[Path] = []

        try:
            # Сначала готовим все временные файлы. Ошибка кодировки или
            # диска не должна оставить половину патча записанной.
            for file in generated.files:
                destination = root / Path(file.relative_path)

                if destination.exists() and not overwrite:
                    raise PatchWriteError(
                        "Файл уже существует: %s" % destination
                    )

                destination.parent.mkdir(parents=True, exist_ok=True)

                data = file.text.encode(file.encoding, errors="strict")
                fd, temp_name = tempfile.mkstemp(
                    prefix=".ace_patch_",
                    suffix=".tmp",
                    dir=str(destination.parent),
                )
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)

                prepared.append((Path(temp_name), destination))

            for temp_name, destination in prepared:
                os.replace(str(temp_name), str(destination))
                written.append(destination)
        except Exception:
            for temp_name, _destination in prepared:
                try:
                    if temp_name.exists():
                        temp_name.unlink()
                except OSError:
                    pass
            raise

        return tuple(written)

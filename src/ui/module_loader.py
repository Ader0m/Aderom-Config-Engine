# -*- coding: utf-8 -*-
"""Обнаружение и загрузка модулей.

Загрузчик не просто импортирует пакеты: он строит граф зависимостей
``manifest.requires``, загружает зависимости раньше зависимых и откатывает
инициализацию модуля, если ``init``/``register`` не удались.
"""

import importlib
import pkgutil
import traceback

import modules as modules_package

from sdk.contracts import ModuleBase
from sdk.version import SDK_VERSION, is_compatible


class ModuleLoadError(Exception):
    pass


class _LoadedModule:
    __slots__ = ("manifest", "instance", "ctx", "package")

    def __init__(self, manifest, instance, ctx, package):
        self.manifest = manifest
        self.instance = instance
        self.ctx = ctx
        self.package = package


class ModuleLoader:
    def __init__(self, make_context):
        self.make_context = make_context
        # Сохраняем совместимый с прежним кодом формат:
        # (manifest, instance, ctx).
        self.loaded = []

    # -------------------------------------------------- обнаружение
    def discover_and_load(self, log=None):
        log = log or (lambda m: None)
        infos = [
            info
            for info in pkgutil.iter_modules(modules_package.__path__)
            if info.ispkg
        ]
        candidates = {}
        for info in infos:
            entry = self._import_module(info.name, log)
            if entry is None:
                continue
            name = entry.manifest.name
            if name in candidates:
                log(
                    "Модуль %s: manifest.name '%s' уже занят пакетом %s."
                    % (info.name, name, candidates[name].package)
                )
                continue
            candidates[name] = entry

        ordered = self._dependency_order(candidates, log)
        for entry in ordered:
            self._start_module(entry, candidates, log)

    def _import_module(self, package_name, log):
        try:
            mod = importlib.import_module(
                "modules.%s" % package_name
            )
        except Exception:
            log(
                "Модуль %s: ошибка импорта.\n%s"
                % (package_name, traceback.format_exc())
            )
            return None

        instance = getattr(mod, "MODULE", None)
        if instance is None and hasattr(mod, "create_module"):
            instance = mod.create_module()

        if not isinstance(instance, ModuleBase):
            log(
                "Модуль %s: не найден MODULE типа ModuleBase."
                % package_name
            )
            return None

        manifest = instance.manifest
        if manifest is None:
            log("Модуль %s: нет манифеста." % package_name)
            return None

        if not is_compatible(manifest.sdk_min):
            log(
                "Модуль %s: несовместим (нужен SDK >= %s, текущий %s)."
                % (package_name, manifest.sdk_min, SDK_VERSION)
            )
            return None

        return _LoadedModule(manifest, instance, None, package_name)

    # -------------------------------------------------- зависимости
    def _dependency_order(self, candidates, log):
        """Kahn's algorithm; при цикле оставшиеся модули не загружаются."""
        names = set(candidates)
        indegree = {name: 0 for name in names}
        edges = {name: [] for name in names}
        missing = {}

        for name, entry in candidates.items():
            deps = tuple(
                getattr(entry.manifest, "requires", ()) or ()
            )
            absent = []
            for dep in deps:
                if dep not in candidates:
                    absent.append(dep)
                    continue
                edges[dep].append(name)
                indegree[name] += 1
            if absent:
                missing[name] = absent

        for name, deps in sorted(missing.items()):
            log(
                "Модуль %s: не загружены зависимости: %s"
                % (name, ", ".join(deps))
            )
            names.discard(name)

        # Зависимые от пропущенного модуля тоже пропускаем.
        changed = True
        while changed:
            changed = False
            for name in list(names):
                deps = tuple(
                    getattr(candidates[name].manifest, "requires", ()) or ()
                )
                if any(dep not in names for dep in deps):
                    log(
                        "Модуль %s: зависимость будет пропущена." % name
                    )
                    names.discard(name)
                    changed = True

        ready = sorted(
            (name for name in names if indegree.get(name, 0) == 0),
            key=lambda name: candidates[name].package.lower(),
        )
        ordered = []
        while ready:
            name = ready.pop(0)
            if name not in names:
                continue
            names.discard(name)
            ordered.append(candidates[name])
            for child in sorted(
                edges.get(name, ()),
                key=lambda item: candidates[item].package.lower(),
            ):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
            ready.sort(
                key=lambda item: candidates[item].package.lower()
            )

        if names:
            log(
                "Модули не загружены из-за циклических зависимостей: %s"
                % ", ".join(sorted(names))
            )

        return ordered

    # -------------------------------------------------- запуск
    def _start_module(self, entry, candidates, log):
        loaded_names = {
            loaded.manifest.name for loaded in self._records()
        }
        missing = [
            dependency
            for dependency in (entry.manifest.requires or ())
            if dependency not in loaded_names
        ]
        if missing:
            log(
                "Модуль %s: не загружены зависимости: %s"
                % (entry.manifest.name, ", ".join(missing))
            )
            return

        try:
            ctx = self.make_context(entry.manifest)
        except Exception:
            log(
                "Модуль %s: ошибка создания контекста.\n%s"
                % (entry.manifest.name, traceback.format_exc())
            )
            return

        try:
            entry.instance.init(ctx)
            entry.instance.register(ctx)
        except Exception:
            log(
                "Модуль %s: ошибка init/register.\n%s"
                % (entry.manifest.name, traceback.format_exc())
            )
            self._rollback(entry, ctx, log)
            return

        entry.ctx = ctx
        self.loaded.append((entry.manifest, entry.instance, ctx))
        log(
            "Модуль загружен: %s %s (SDK min %s)"
            % (
                entry.manifest.name,
                entry.manifest.version,
                entry.manifest.sdk_min,
            )
        )

    def _rollback(self, entry, ctx, log):
        """Отозвать всё, что модуль успел создать до ошибки."""
        try:
            entry.instance.deactivate(ctx)
        except Exception:
            log(
                "Модуль %s: ошибка отката deactivate.\n%s"
                % (entry.manifest.name, traceback.format_exc())
            )

    def _records(self):
        return [
            _LoadedModule(manifest, instance, ctx, "")
            for manifest, instance, ctx in self.loaded
        ]

    # -------------------------------------------------- фазы
    def activate_all(self, log=None):
        log = log or (lambda m: None)
        for manifest, instance, ctx in self.loaded:
            try:
                instance.activate(ctx)
            except Exception:
                log(
                    "Модуль %s: ошибка activate.\n%s"
                    % (manifest.name, traceback.format_exc())
                )

    def deactivate_all(self, log=None):
        log = log or (lambda m: None)
        for manifest, instance, ctx in reversed(self.loaded):
            try:
                instance.deactivate(ctx)
            except Exception:
                log(
                    "Модуль %s: ошибка deactivate.\n%s"
                    % (manifest.name, traceback.format_exc())
                )

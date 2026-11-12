# -*- coding: utf-8 -*-
"""Обнаружение и загрузка модулей."""

import importlib
import pkgutil
import traceback

import modules as modules_package

from sdk.contracts import ModuleBase
from sdk.version import SDK_VERSION, is_compatible


class ModuleLoadError(Exception):
    pass


class ModuleLoader:
    def __init__(self, make_context):
        self.make_context = make_context
        self.loaded = []

    def discover_and_load(self, log=None):
        log = log or (lambda m: None)

        infos = [
            info
            for info in pkgutil.iter_modules(modules_package.__path__)
            if info.ispkg
        ]
        # base_module — обязательная платформа; остальные модули могут
        # объявлять его в manifest.requires.
        infos.sort(
            key=lambda info: (
                info.name != "base_module",
                info.name,
            )
        )
        for info in infos:
            self._load_one(info.name, log)

    def _load_one(self, name, log):
        try:
            mod = importlib.import_module("modules.%s" % name)
        except Exception:
            log("Модуль %s: ошибка импорта.\n%s" % (name, traceback.format_exc()))
            return

        instance = getattr(mod, "MODULE", None)
        if instance is None and hasattr(mod, "create_module"):
            instance = mod.create_module()

        if not isinstance(instance, ModuleBase):
            log("Модуль %s: не найден MODULE типа ModuleBase." % name)
            return

        manifest = instance.manifest
        if manifest is None:
            log("Модуль %s: нет манифеста." % name)
            return

        if not is_compatible(manifest.sdk_min):
            log(
                "Модуль %s: несовместим (нужен SDK >= %s, текущий %s)."
                % (name, manifest.sdk_min, SDK_VERSION)
            )
            return

        loaded_names = {
            loaded_manifest.name
            for loaded_manifest, _instance, _ctx in self.loaded
        }
        missing = [
            dependency
            for dependency in (getattr(manifest, "requires", ()) or ())
            if dependency not in loaded_names
        ]
        if missing:
            log(
                "Модуль %s: не загружены зависимости: %s"
                % (name, ", ".join(missing))
            )
            return

        ctx = self.make_context(manifest)

        try:
            instance.init(ctx)
            instance.register(ctx)
        except Exception:
            log("Модуль %s: ошибка init/register.\n%s" % (name, traceback.format_exc()))
            return

        self.loaded.append((manifest, instance, ctx))
        log(
            "Модуль загружен: %s %s (SDK min %s)"
            % (manifest.name, manifest.version, manifest.sdk_min)
        )

    def activate_all(self, log=None):
        log = log or (lambda m: None)
        for manifest, instance, ctx in self.loaded:
            try:
                instance.activate(ctx)
            except Exception:
                log("Модуль %s: ошибка activate.\n%s" % (manifest.name, traceback.format_exc()))

    def deactivate_all(self, log=None):
        log = log or (lambda m: None)
        for manifest, instance, ctx in reversed(self.loaded):
            try:
                instance.deactivate(ctx)
            except Exception:
                log("Модуль %s: ошибка deactivate.\n%s" % (manifest.name, traceback.format_exc()))
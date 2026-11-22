# -*- coding: utf-8 -*-
"""Обнаружение, загрузка и выгрузка модулей.

Загрузчик строит граф зависимостей по ``manifest.requires``,
``optional_requires``, ``provides``/``needs``, проверяет версии, ведёт
явное состояние каждого модуля и умеет его выгрузить/перезагрузить.

Порядок фаз:

    discover -> resolve -> init -> register -> activate -> ... -> deactivate

Ошибка ``init``/``register``/``activate`` переводит модуль в ``FAILED``:
вызывается ``deactivate``, снимаются регистрации (``RegistrationScope``),
закрываются ресурсы модуля и удаляется импортированный пакет.
"""

import importlib
import pkgutil
import sys
import traceback
from dataclasses import dataclass
from enum import Enum

import modules as modules_package

from sdk.kernel import ModuleBase, ModuleRequirement, normalize_requirements
from sdk.kernel.capabilities import (
    CapabilityConflictError,
    CapabilityRegistry,
    CapabilityScope,
)
from sdk.kernel.levels import check_manifest
from sdk.kernel.version import SDK_VERSION, version_at_least


class ModuleLoadError(Exception):
    pass


class ModuleState(str, Enum):
    IMPORTED = "imported"
    INITIALIZED = "initialized"
    REGISTERED = "registered"
    ACTIVE = "active"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNLOADED = "unloaded"


@dataclass(frozen=True)
class ModuleSkip:
    """Причина, по которой модуль не загружен или не активирован."""

    kind: str
    target: str = ""
    detail: str = ""


@dataclass
class ModuleRecord:
    """Состояние одного модуля в загрузчике."""

    manifest: object
    instance: object
    package: str
    ctx: object = None
    state: ModuleState = ModuleState.IMPORTED
    error: str = ""
    # Обязательные зависимости (имена модулей), уже разрешённые.
    dependencies: tuple = ()
    # Необязательные зависимости; не влияют на порядок и активацию.
    optional_dependencies: tuple = ()
    # Все зависимые модули (для unload/reload).
    dependents: tuple = ()
    provides: tuple = ()
    needs: tuple = ()

    @property
    def name(self):
        return self.manifest.name


class ModuleLoader:
    def __init__(self, make_context, package=None):
        self.make_context = make_context
        self.package = package or modules_package

        # Полное состояние, включая FAILED/SKIPPED/UNLOADED.
        self.records = {}
        self.skipped = {}
        self.capabilities = {}
        self.capability_conflicts = {}
        self.capability_registry = CapabilityRegistry()
        self._order = []
        self._candidates = {}

    # -------------------------------------------------- обнаружение
    def discover(self, log=None):
        log = log or (lambda m: None)
        infos = [
            info
            for info in pkgutil.iter_modules(self.package.__path__)
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
        return candidates

    def discover_and_load(self, log=None):
        log = log or (lambda m: None)
        self.skipped = {}
        candidates = self.discover(log)
        ordered = self._dependency_order(candidates, log)
        self._candidates = candidates
        for entry in ordered:
            self._start_module(entry, candidates, log)
        self._rebuild_capabilities(log)
        self._compute_dependents()
        self._refresh_contexts()

    def _import_module(self, package_name, log):
        try:
            mod = importlib.import_module(
                "%s.%s" % (self.package.__name__, package_name)
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

        if not check_manifest(manifest, SDK_VERSION):
            log(
                "Модуль %s: несовместим (нужен SDK >= %s, текущий %s)."
                % (package_name, manifest.sdk_min, SDK_VERSION)
            )
            return None

        return ModuleRecord(
            manifest=manifest,
            instance=instance,
            package=package_name,
            provides=_capability_values(manifest.provides),
            needs=_capability_values(manifest.needs),
        )

    # -------------------------------------------------- зависимости
    def _dependency_order(self, candidates, log):
        """Разрешить граф зависимостей и вернуть порядок загрузки.

        Отсутствующие и несовместимые обязательные зависимости
        исключают модуль и всех, кто от него зависит. Optional-ребра
        учитываются только при наличии провайдера.
        """
        names = set(candidates)
        self.capabilities = self._build_capabilities(candidates, log)

        edges = {name: set() for name in names}
        indegree = {name: 0 for name in names}
        dependencies = {name: set() for name in names}
        optional_dependencies = {name: set() for name in names}
        reasons = {}

        def add_reason(name, skip):
            reasons.setdefault(name, [])
            if skip not in reasons[name]:
                reasons[name].append(skip)

        def connect(provider, consumer, required=True):
            if provider == consumer:
                if required:
                    add_reason(
                        consumer,
                        ModuleSkip(
                            "self_dependency",
                            provider,
                            "модуль не может зависеть от себя",
                        ),
                    )
                return False
            if not required:
                optional_dependencies[consumer].add(provider)
                return True
            if consumer not in edges[provider]:
                edges[provider].add(consumer)
                indegree[consumer] += 1
            dependencies[consumer].add(provider)
            return True

        for name, entry in candidates.items():
            manifest = entry.manifest
            for value in normalize_requirements(manifest.requires):
                provider = candidates.get(value.name)
                if provider is None:
                    add_reason(
                        name,
                        ModuleSkip(
                            "missing_required",
                            value.name,
                            "обязательный модуль не найден",
                        ),
                    )
                    continue
                provider_version = str(provider.manifest.version)
                if (
                    value.min_version
                    and not version_at_least(
                        provider_version,
                        value.min_version,
                    )
                ):
                    add_reason(
                        name,
                        ModuleSkip(
                            "version_conflict",
                            value.name,
                            "нужен >= %s, найден %s"
                            % (value.min_version, provider_version),
                        ),
                    )
                    continue
                connect(value.name, name)

            for value in normalize_requirements(
                manifest.optional_requires
            ):
                provider = candidates.get(value.name)
                if provider is None:
                    continue
                provider_version = str(provider.manifest.version)
                if (
                    value.min_version
                    and not version_at_least(
                        provider_version,
                        value.min_version,
                    )
                ):
                    log(
                        "Модуль %s: optional-зависимость %s несовместима"
                        % (name, value.name)
                    )
                    continue
                connect(value.name, name, required=False)

            for capability in _capability_values(manifest.needs):
                provider_name = self.capabilities.get(str(capability))
                if provider_name is None:
                    add_reason(
                        name,
                        ModuleSkip(
                            "missing_capability",
                            str(capability),
                            "возможность не предоставлена",
                        ),
                    )
                    continue
                connect(provider_name, name)

            for capability in _capability_values(manifest.optional_needs):
                provider_name = self.capabilities.get(str(capability))
                if provider_name is not None:
                    connect(provider_name, name, required=False)

        # Модули с неразрешимыми обязательными зависимостями не грузим.
        for name in sorted(reasons):
            for skip in reasons[name]:
                self.skipped.setdefault(name, [])
                if skip not in self.skipped[name]:
                    self.skipped[name].append(skip)
            names.discard(name)

        # Зависимые от пропущенного модуля тоже пропускаем.
        changed = True
        while changed:
            changed = False
            for name in list(names):
                absent = [
                    dep for dep in dependencies[name]
                    if dep not in names
                ]
                if not absent:
                    continue
                for dep in absent:
                    add_reason(
                        name,
                        ModuleSkip(
                            "dependency_skipped",
                            dep,
                            "зависимость будет пропущена",
                        ),
                    )
                for skip in reasons.get(name, ()):
                    self.skipped.setdefault(name, [])
                    if skip not in self.skipped[name]:
                        self.skipped[name].append(skip)
                log("Модуль %s: зависимость будет пропущена." % name)
                names.discard(name)
                changed = True

        # Сохраняем зависимости в записях.
        for name in names:
            entry = candidates[name]
            if hasattr(entry, "dependencies"):
                entry.dependencies = tuple(sorted(dependencies[name]))
            if hasattr(entry, "optional_dependencies"):
                entry.optional_dependencies = tuple(
                    sorted(optional_dependencies[name])
                )

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
            cycle = sorted(names)
            log(
                "Модули не загружены из-за циклических зависимостей: %s"
                % ", ".join(cycle)
            )
            for name in cycle:
                self.skipped.setdefault(name, []).append(
                    ModuleSkip(
                        "cycle",
                        name,
                        "циклическая зависимость",
                    )
                )
        return ordered

    def _build_capabilities(self, candidates, log=None):
        """Собрать карту capability -> провайдер.

        Конфликт двух провайдеров не разрешается «первым победителем»:
        возможность исключается из карты, а конфликт логируется и
        сохраняется в ``capability_conflicts``.
        """
        capabilities = {}
        conflicts = {}
        for name in sorted(
            candidates,
            key=lambda item: candidates[item].package.lower(),
        ):
            for capability in _capability_values(
                candidates[name].manifest.provides
            ):
                key = str(capability)
                current = capabilities.get(key)
                if current is None:
                    capabilities[key] = name
                    continue
                if current == name:
                    continue
                providers = conflicts.setdefault(key, [current])
                if name not in providers:
                    providers.append(name)

        for key, providers in conflicts.items():
            capabilities.pop(key, None)
            if log is not None:
                log(
                    "Конфликт capability %s: модули %s. "
                    "Провайдер не выбран."
                    % (key, ", ".join(providers))
                )
        self.capability_conflicts = conflicts
        return capabilities

    def _rebuild_capabilities(self, log=None):
        current = {
            name: record
            for name, record in self.records.items()
            if record.state in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            )
        }
        self.capabilities = self._build_capabilities(current, log)
        self._sync_capability_registry(log)

    def _sync_capability_registry(self, log=None):
        for name, record in self.records.items():
            if record.state not in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            ):
                self.capability_registry.remove_owner(name)
        for name, record in self.records.items():
            if record.state not in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            ):
                continue
            for capability_name, version in _capability_specs(
                record.manifest.provides
            ):
                if capability_name in self.capability_conflicts:
                    self.capability_registry.discard(capability_name)
                    continue
                try:
                    self.capability_registry.declare(
                        capability_name,
                        version,
                        owner=name,
                    )
                except CapabilityConflictError as exc:
                    self.capability_registry.discard(capability_name)
                    self.capabilities.pop(capability_name, None)
                    providers = self.capability_conflicts.setdefault(
                        capability_name,
                        [],
                    )
                    if name not in providers:
                        providers.append(name)
                    if log is not None:
                        log("Конфликт capability: %s" % exc)

    def _compute_dependents(self):
        dependents = {name: set() for name in self.records}
        for name, record in self.records.items():
            for dependency in getattr(record, "dependencies", ()) or ():
                if dependency in dependents:
                    dependents[dependency].add(name)
        for name, record in self.records.items():
            record.dependents = tuple(sorted(dependents.get(name, ())))
        return dependents

    def _refresh_contexts(self):
        """Обновить снимки зависимостей в уже созданных контекстах."""
        modules = {
            name: record.manifest.version
            for name, record in self.records.items()
            if record.state in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            )
        }
        for record in self.records.values():
            ctx = record.ctx
            if ctx is None:
                continue
            try:
                ctx.kernel.available_modules = dict(modules)
            except Exception:
                pass

    # -------------------------------------------------- запуск
    def _start_module(self, entry, candidates, log):
        record = entry if isinstance(entry, ModuleRecord) else ModuleRecord(
            manifest=entry.manifest,
            instance=entry.instance,
            package=entry.package,
        )
        name = record.name

        if not record.dependencies:
            record.dependencies = self._required_dependencies(record)

        loaded = self.records.get(name)
        if loaded is not None and loaded.state in (
            ModuleState.REGISTERED,
            ModuleState.ACTIVE,
        ):
            log("Модуль %s: уже загружен." % name)
            return True

        self._rebuild_capabilities(log)
        missing = self._missing_dependencies(record)
        if missing:
            record.state = ModuleState.SKIPPED
            self.records[name] = record
            self.capability_registry.remove_owner(name)
            self._rebuild_capabilities(log)
            self.skipped[name] = [
                ModuleSkip("missing_required", dependency,
                           "зависимость не загружена")
                for dependency in missing
            ]
            log(
                "Модуль %s: не загружены зависимости: %s"
                % (name, ", ".join(missing))
            )
            return False

        try:
            ctx = self.make_context(record.manifest)
        except Exception:
            record.state = ModuleState.FAILED
            record.error = "ошибка создания контекста"
            self.records[name] = record
            self.capability_registry.remove_owner(name)
            self._rebuild_capabilities(log)
            log(
                "Модуль %s: ошибка создания контекста.\n%s"
                % (name, traceback.format_exc())
            )
            self._unload_package(record, log)
            return False

        record.ctx = ctx
        if ctx is not None:
            try:
                ctx.capabilities = CapabilityScope(
                    self.capability_registry,
                    ctx.module_id,
                )
                ctx.kernel.capabilities = ctx.capabilities
                ctx.kernel.available_modules = {
                    module_name: module_record.manifest.version
                    for module_name, module_record in self.records.items()
                    if module_record.state in (
                        ModuleState.REGISTERED,
                        ModuleState.ACTIVE,
                    )
                }
            except Exception:
                pass

        try:
            record.instance.init(ctx)
            record.state = ModuleState.INITIALIZED
            record.instance.register(ctx)
            record.state = ModuleState.REGISTERED
        except Exception:
            record.state = ModuleState.FAILED
            record.error = "ошибка init/register"
            log(
                "Модуль %s: ошибка init/register.\n%s"
                % (name, traceback.format_exc())
            )
            self._rollback(record, ctx, log)
            return False

        self.records[name] = record
        if name not in self._order:
            self._order.append(name)
        self._rebuild_capabilities(log)
        log(
            "Модуль загружен: %s %s (SDK min %s)"
            % (
                record.manifest.name,
                record.manifest.version,
                record.manifest.sdk_min,
            )
        )
        return True

    def _required_dependencies(self, record):
        names = set()
        for value in normalize_requirements(record.manifest.requires):
            names.add(value.name)
        for capability in _capability_values(record.manifest.needs):
            provider = self.capabilities.get(str(capability))
            if provider and provider != record.name:
                names.add(provider)
        return tuple(sorted(names))

    def _missing_dependencies(self, record):
        missing = []
        for value in normalize_requirements(record.manifest.requires):
            if not self._dependency_ready(value):
                missing.append(value.name)
        for capability in _capability_values(record.manifest.needs):
            provider = self.capabilities.get(str(capability))
            if provider is None:
                missing.append(str(capability))
                continue
            if not self._dependency_ready(
                ModuleRequirement(provider)
            ):
                missing.append(provider)
        return tuple(sorted(set(missing)))

    def _dependency_ready(self, requirement):
        record = self.records.get(requirement.name)
        if record is None or record.state not in (
            ModuleState.REGISTERED,
            ModuleState.ACTIVE,
        ):
            return False
        if not requirement.min_version:
            return True
        return version_at_least(
            record.manifest.version,
            requirement.min_version,
        )

    def _deactivate_entry(self, entry, ctx, log):
        """Вызвать deactivate и снять все оставшиеся регистрации модуля."""
        try:
            entry.instance.deactivate(ctx)
        except Exception:
            log(
                "Модуль %s: ошибка deactivate.\n%s"
                % (entry.manifest.name, traceback.format_exc())
            )
        finally:
            scope = getattr(ctx, "registrations", None)
            if scope is not None:
                try:
                    removed = scope.dispose()
                except Exception:
                    log(
                        "Модуль %s: ошибка очистки регистраций.\n%s"
                        % (entry.manifest.name, traceback.format_exc())
                    )
                else:
                    if removed:
                        log(
                            "Модуль %s: снято регистраций %d"
                            % (entry.manifest.name, removed)
                        )
            self._release_runtime(entry, ctx, log)
            removed_caps = self.capability_registry.remove_owner(
                entry.manifest.name
            )
            if removed_caps:
                log(
                    "Модуль %s: снято capability %d"
                    % (entry.manifest.name, removed_caps)
                )

    def _release_runtime(self, entry, ctx, log):
        """Закрыть окна/вкладки и дождаться задач, принадлежащих модулю."""
        ui = getattr(ctx, "ui", None)
        release = getattr(ui, "release_owner", None)
        if callable(release):
            try:
                release()
            except Exception:
                log(
                    "Модуль %s: ошибка закрытия ресурсов UI.\n%s"
                    % (entry.manifest.name, traceback.format_exc())
                )

        tasks = getattr(ctx, "tasks", None)
        cancel = getattr(tasks, "cancel_owner", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                log(
                    "Модуль %s: ошибка отмены задач.\n%s"
                    % (entry.manifest.name, traceback.format_exc())
                )
        wait = getattr(tasks, "wait_owner", None)
        if callable(wait):
            try:
                finished = wait(timeout_ms=5000)
            except TypeError:
                finished = wait()
            except Exception:
                log(
                    "Модуль %s: ошибка ожидания задач.\n%s"
                    % (entry.manifest.name, traceback.format_exc())
                )
                finished = True
            if not finished:
                log(
                    "Модуль %s: фоновые задачи не завершились"
                    % entry.manifest.name
                )

    def _rollback(self, entry, ctx, log):
        """Отозвать всё, что модуль успел создать до ошибки."""
        self._deactivate_entry(entry, ctx, log)
        entry.state = ModuleState.FAILED
        self._drop_order(entry)
        self._rebuild_capabilities(log)
        self._unload_package(entry, log)

    def _drop_order(self, record):
        if record.name in self._order:
            self._order.remove(record.name)

    def _records(self):
        return list(self.records.values())

    # -------------------------------------------------- фазы
    def activate_all(self, log=None):
        log = log or (lambda m: None)
        for name in list(self._order):
            record = self.records.get(name)
            if record is None or record.state is not ModuleState.REGISTERED:
                continue
            missing = [
                dependency
                for dependency in (record.dependencies or ())
                if self.records.get(dependency) is None
                or self.records[dependency].state is not ModuleState.ACTIVE
            ]
            if missing:
                record.state = ModuleState.SKIPPED
                log(
                    "Модуль %s: зависимости не активированы: %s"
                    % (name, ", ".join(missing))
                )
                self._rollback(record, record.ctx, log)
                self.records.pop(name, None)
                continue

            try:
                record.instance.activate(record.ctx)
                record.state = ModuleState.ACTIVE
            except Exception:
                log(
                    "Модуль %s: ошибка activate.\n%s"
                    % (name, traceback.format_exc())
                )
                self._rollback(record, record.ctx, log)
                self.records.pop(name, None)
        self._compute_dependents()

    def deactivate_all(self, log=None):
        log = log or (lambda m: None)
        for name in reversed(list(self._order)):
            record = self.records.get(name)
            if record is None:
                continue
            if record.state not in (
                ModuleState.ACTIVE,
                ModuleState.REGISTERED,
                ModuleState.INITIALIZED,
            ):
                continue
            self._deactivate_entry(record, record.ctx, log)
            record.state = ModuleState.UNLOADED
        self._rebuild_capabilities(log)
        self._compute_dependents()
        self._refresh_contexts()

    # -------------------------------------------------- выгрузка
    def unload(self, name, log=None, force=False):
        """Выгрузить модуль; при ``force`` сначала выгружаются зависимые."""
        log = log or (lambda m: None)
        record = self.records.get(str(name))
        if record is None:
            return False

        dependents = [
            dependent
            for dependent in (record.dependents or ())
            if self.records.get(dependent) is not None
            and self.records[dependent].state in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            )
        ]
        if dependents and not force:
            log(
                "Модуль %s: нельзя выгрузить, зависят: %s"
                % (record.name, ", ".join(dependents))
            )
            return False
        if force:
            for dependent in reversed(dependents):
                self.unload(dependent, log=log, force=True)

        if record.state in (
            ModuleState.ACTIVE,
            ModuleState.REGISTERED,
            ModuleState.INITIALIZED,
        ):
            self._deactivate_entry(record, record.ctx, log)

        record.state = ModuleState.UNLOADED
        self._drop_order(record)
        self.records.pop(record.name, None)
        self._candidates.pop(record.name, None)
        self.skipped.pop(record.name, None)
        self._unload_package(record, log)
        self._rebuild_capabilities(log)
        self._compute_dependents()
        self._refresh_contexts()
        log("Модуль выгружен: %s" % record.name)
        return True

    def reload(self, name, log=None):
        """Выгрузить и снова загрузить модуль без перезапуска приложения."""
        log = log or (lambda m: None)
        record = self.records.get(str(name))
        if record is None:
            return False

        dependents = [
            dependent
            for dependent in (record.dependents or ())
            if self.records.get(dependent) is not None
            and self.records[dependent].state in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            )
        ]
        if dependents:
            log(
                "Модуль %s: нельзя перезагрузить, зависят: %s"
                % (record.name, ", ".join(dependents))
            )
            return False

        was_active = record.state is ModuleState.ACTIVE
        package = record.package
        if not self.unload(name, log=log):
            return False

        entry = self._import_module(package, log)
        if entry is None:
            return False
        if not self._start_module(entry, {}, log):
            return False
        if was_active:
            self.activate_all(log)
        self._compute_dependents()
        self._refresh_contexts()
        return self.records.get(entry.name) is not None

    # -------------------------------------------------- импорт
    def _unload_package(self, record, log):
        package = str(getattr(record, "package", "") or "")
        if not package:
            return
        prefix = "%s.%s" % (self.package.__name__, package)
        removed = [
            module_name
            for module_name in list(sys.modules)
            if module_name == prefix or module_name.startswith(prefix + ".")
        ]
        for module_name in removed:
            sys.modules.pop(module_name, None)
        if removed:
            importlib.invalidate_caches()
            log(
                "Модуль %s: выгружено подмодулей %d"
                % (record.manifest.name, len(removed))
            )


def _capability_values(values):
    if isinstance(values, str):
        return (values,)
    return tuple(str(value) for value in (values or ()))


def _capability_specs(values):
    """Вернуть пары (name, version) из манифеста."""
    if isinstance(values, str):
        values = (values,)
    result = []
    for value in values or ():
        if isinstance(value, (tuple, list)) and value:
            name = str(value[0])
            version = str(value[1]) if len(value) > 1 else "1.0.0"
        else:
            name = str(value)
            version = "1.0.0"
        result.append((name, version))
    return tuple(result)

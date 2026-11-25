# -*- coding: utf-8 -*-
"""Обнаружение, загрузка и выгрузка модулей.

Загрузчик читает и валидирует ``module.json`` до импорта кода, строит
граф зависимостей по ``requires``/``optional_requires`` и
``provides``/``needs``, проверяет версии, ведёт явное состояние каждого
модуля и умеет его выгрузить/перезагрузить. Class-manifest и
``create_module()`` не поддерживаются (ADR-31).

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
from pathlib import Path

import modules as modules_package

from sdk.kernel import ModuleBase, ModuleRequirement, normalize_requirements
from sdk.kernel.capabilities import (
    CapabilityConflictError,
    CapabilityRegistry,
    capability_version_ok,
)
from sdk.kernel.levels import check_manifest
from sdk.kernel.version import SDK_VERSION, version_at_least
from ui.platform.module_manifest import (
    ModuleManifestError,
    read_module_manifest,
)
from ui.platform.sdk_bridge.capabilities import ModuleCapabilities


class ModuleLoadError(Exception):
    pass


class ModuleState(str, Enum):
    DISCOVERED = "discovered"
    VALIDATED = "validated"
    LOADED = "loaded"
    INITIALIZED = "initialized"
    REGISTERED = "registered"
    ACTIVE = "active"
    FAILED = "failed"
    SKIPPED = "skipped"
    DISABLED = "disabled"
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
    state: ModuleState = ModuleState.DISCOVERED
    error: str = ""
    # Фаза, на которой модуль получил FAILED: import/init/register/activate.
    phase: str = ""
    # Публичный ctx.capabilities этого модуля (для close при выгрузке).
    capabilities: object = None
    # Обязательные зависимости (имена модулей), уже разрешённые.
    dependencies: tuple = ()
    # Необязательные зависимости; не влияют на порядок и активацию.
    optional_dependencies: tuple = ()
    # Все зависимые модули (для unload/reload).
    dependents: tuple = ()
    # Активные/зарегистрированные optional-зависимые.
    optional_dependents: tuple = ()
    provides: tuple = ()
    needs: tuple = ()

    @property
    def name(self):
        return self.manifest.id


class ModuleLoader:
    def __init__(
        self,
        make_context,
        package=None,
        trusted_ids=None,
        config=None,
        required_ids=None,
    ):
        self.make_context = make_context
        self.package = package or modules_package
        # Платформенная политика доверия: id, которым разрешено объявлять
        # trust=core/trusted. None означает «пакеты этого корня —
        # поставляемые»; composition root передаёт явный набор.
        self.trusted_ids = (
            None if trusted_ids is None else frozenset(trusted_ids)
        )
        # Пользовательское включение/отключение (config/modules.json).
        self.config = config
        # Обязательные модули проекта (ADR-35) нельзя отключить.
        self.required_ids = frozenset(required_ids or ())

        # Полное состояние, включая FAILED/SKIPPED/UNLOADED.
        self.records = {}
        self.skipped = {}
        self.capabilities = {}
        self.capability_versions = {}
        self.capability_conflicts = {}
        self.capability_registry = CapabilityRegistry()
        self._order = []
        self._candidates = {}
        self._running = False

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
            entry = self._read_entry(info.name, log)
            if entry is None:
                continue
            name = entry.manifest.id
            if name in candidates:
                log(
                    "Модуль %s: id '%s' уже занят пакетом %s."
                    % (info.name, name, candidates[name].package)
                )
                continue
            candidates[name] = entry
        self._candidates = candidates
        return candidates

    def discover_and_load(self, log=None):
        log = log or (lambda m: None)
        self.skipped = {}
        candidates = self.discover(log)
        ordered = self._dependency_order(candidates, log)
        self._candidates = candidates
        for entry in ordered:
            if not self._import_entry(entry, log):
                continue
            self._start_module(entry, candidates, log)
        self._rebuild_capabilities(log)
        self._compute_dependents()
        self._refresh_contexts()

    def _package_dir(self, package_name):
        return Path(list(self.package.__path__)[0]) / str(package_name)

    def _skip(self, name, kind, target, detail=""):
        self.skipped.setdefault(str(name), [])
        skip = ModuleSkip(kind, target, detail)
        if skip not in self.skipped[str(name)]:
            self.skipped[str(name)].append(skip)

    def _read_entry(self, package_name, log):
        """Прочитать и проверить манифест, не импортируя код модуля.

        Отсутствующий, несовместимый или недоверенный манифест означает
        ``SKIPPED`` без выполнения кода. Импорт entrypoint выполняется
        отдельно, только после проверки зависимостей и capability.
        """
        try:
            manifest = read_module_manifest(
                self._package_dir(package_name),
                warn=log,
            )
        except ModuleManifestError as exc:
            self._skip(
                package_name,
                exc.kind,
                package_name,
                exc.detail,
            )
            log(
                "Модуль %s: манифест отклонён (%s): %s"
                % (package_name, exc.kind, exc.detail)
            )
            return None

        enabled = manifest.enabled_by_default
        if self.config is not None:
            enabled = self.config.enabled_for(manifest.id, enabled)
            if manifest.id in self.required_ids and not enabled:
                log(
                    "Модуль %s: обязательный модуль нельзя отключить; "
                    "запись config/modules.json проигнорирована."
                    % manifest.id
                )
                enabled = True
        if not enabled:
            record = ModuleRecord(
                manifest=manifest,
                instance=None,
                package=package_name,
                state=ModuleState.DISABLED,
            )
            self.records[manifest.id] = record
            self.skipped.pop(manifest.id, None)
            log(
                "Модуль %s: DISABLED пользовательской конфигурацией."
                % manifest.id
            )
            return None

        if (
            self.trusted_ids is not None
            and manifest.id not in self.trusted_ids
        ):
            self._reject(
                manifest,
                package_name,
                "trust_untrusted",
                manifest.id,
                "id не входит в платформенный список доверенных",
                log,
            )
            return None

        if manifest.trust == "untrusted":
            self._reject(
                manifest,
                package_name,
                "trust_untrusted",
                manifest.id,
                "модуль с trust=untrusted не загружается",
                log,
            )
            return None

        platform = _current_platform()
        if platform not in manifest.platforms:
            self._reject(
                manifest,
                package_name,
                "platform_unsupported",
                platform,
                "платформа %s не поддерживается" % platform,
                log,
            )
            return None

        if not check_manifest(manifest, SDK_VERSION):
            self._reject(
                manifest,
                package_name,
                "sdk_incompatible",
                manifest.id,
                "нужен SDK >= %s, текущий %s"
                % (manifest.sdk_min, SDK_VERSION),
                log,
            )
            return None

        optional_dependencies = {
            value.id
            for value in normalize_requirements(manifest.optional_requires)
        }
        for capability, _min_version in _capability_requirements(
            manifest.optional_needs
        ):
            provider = self.capabilities.get(capability)
            if provider:
                optional_dependencies.add(provider)
        return ModuleRecord(
            manifest=manifest,
            instance=None,
            package=package_name,
            provides=_capability_specs(manifest.provides),
            needs=_capability_values(manifest.needs),
            optional_dependencies=tuple(sorted(optional_dependencies)),
        )

    def _reject(
        self,
        manifest,
        package_name,
        kind,
        target,
        detail,
        log,
    ):
        self._skip(manifest.id, kind, target, detail)
        self.records[manifest.id] = ModuleRecord(
            manifest=manifest,
            instance=None,
            package=package_name,
            state=ModuleState.SKIPPED,
        )
        log(
            "Модуль %s: SKIPPED (%s): %s"
            % (manifest.id, kind, detail)
        )

    def _import_entry(self, entry, log):
        """Импортировать entrypoint допущенного модуля.

        Ошибка импорта/`MODULE`/`ModuleBase` оставляет видимую запись
        ``FAILED`` с фазой ``import``; код не активируется.
        """
        if entry.instance is not None:
            return True
        manifest = entry.manifest
        try:
            submodule, attribute = manifest.entrypoint.split(":", 1)
            module = importlib.import_module(
                "%s.%s.%s"
                % (self.package.__name__, entry.package, submodule)
            )
            instance = getattr(module, attribute)
        except Exception as exc:
            self._record_failure(
                entry,
                "import",
                "%s: %s" % (type(exc).__name__, exc),
                log,
                traceback.format_exc(),
            )
            return False

        if not isinstance(instance, ModuleBase):
            self._record_failure(
                entry,
                "import",
                "entrypoint не является ModuleBase",
                log,
            )
            return False

        entry.instance = instance
        entry.state = ModuleState.LOADED
        return True

    def _record_failure(self, entry, phase, error, log, details=""):
        record = entry if isinstance(entry, ModuleRecord) else ModuleRecord(
            manifest=entry.manifest,
            instance=getattr(entry, "instance", None),
            package=entry.package,
        )
        record.state = ModuleState.FAILED
        record.phase = str(phase)
        record.error = str(error)
        self.records[record.name] = record
        self._drop_order(record)
        self._rebuild_capabilities(log)
        self._compute_dependents()
        message = "Модуль %s: FAILED (фаза %s): %s" % (
            record.name,
            record.phase,
            record.error,
        )
        if details:
            log(message + "\n" + details)
        else:
            log(message)
        self._unload_package(record, log)
        return record

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
                            "cycle",
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

        def reaches(start, target):
            stack = [start]
            seen = set()
            while stack:
                node = stack.pop()
                if node == target:
                    return True
                if node in seen:
                    continue
                seen.add(node)
                stack.extend(edges.get(node, ()))
            return False

        # Конфликт провайдеров — ошибка валидации: оба провайдера
        # получают SKIPPED до импорта кода (ADR-32).
        for capability, providers in self.capability_conflicts.items():
            for provider in providers:
                if provider in candidates:
                    add_reason(
                        provider,
                        ModuleSkip(
                            "capability_conflict",
                            capability,
                            "capability объявлена несколькими модулями",
                        ),
                    )

        for name, entry in candidates.items():
            manifest = entry.manifest
            for value in normalize_requirements(manifest.requires):
                provider = candidates.get(value.id)
                if provider is None:
                    add_reason(
                        name,
                        ModuleSkip(
                            "missing_required_module",
                            value.id,
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
                            "missing_required_module",
                            value.id,
                            "нужен >= %s, найден %s"
                            % (value.min_version, provider_version),
                        ),
                    )
                    continue
                connect(value.id, name)

            for value in normalize_requirements(
                manifest.optional_requires
            ):
                provider = candidates.get(value.id)
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
                        % (name, value.id)
                    )
                    continue
                connect(value.id, name, required=False)

            for capability, min_version in _capability_requirements(
                manifest.needs
            ):
                provider_name = self.capabilities.get(capability)
                if provider_name is None:
                    if capability in self.capability_conflicts:
                        kind = "capability_conflict"
                        detail = "конфликт провайдеров capability"
                    else:
                        kind = "missing_capability"
                        detail = "возможность не предоставлена"
                    add_reason(
                        name,
                        ModuleSkip(kind, capability, detail),
                    )
                    continue
                if not _capability_version_ok(
                    self.capability_versions.get(capability, ""),
                    min_version,
                ):
                    add_reason(
                        name,
                        ModuleSkip(
                            "capability_version",
                            capability,
                            "нужна версия >= %s, есть %s"
                            % (
                                min_version,
                                self.capability_versions.get(
                                    capability,
                                    "",
                                ),
                            ),
                        ),
                    )
                    continue
                connect(provider_name, name)

            for capability, min_version in _capability_requirements(
                manifest.optional_needs
            ):
                provider_name = self.capabilities.get(capability)
                if provider_name is None:
                    continue
                if not _capability_version_ok(
                    self.capability_versions.get(capability, ""),
                    min_version,
                ):
                    log(
                        "Модуль %s: optional-capability %s несовместима"
                        % (name, capability)
                    )
                    continue
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

        # Optional-провайдер, который реально допущен, должен загрузиться
        # раньше потребителя: иначе optional capability может быть ещё не
        # зарегистрирована на момент register. Ребро мягкое: если оно
        # создаёт цикл, порядок остаётся алфавитным, а потребитель работает
        # в degraded mode.
        for consumer in sorted(names):
            for provider in sorted(optional_dependencies[consumer]):
                if provider not in names:
                    continue
                if provider in dependencies[consumer]:
                    continue
                if reaches(consumer, provider):
                    continue
                if consumer not in edges[provider]:
                    edges[provider].add(consumer)
                    indegree[consumer] += 1

        # Состояния: все допущенные манифесты прошли валидацию,
        # остальные остаются видимыми как SKIPPED.
        for name, entry in candidates.items():
            if name in names:
                if getattr(entry, "state", None) is ModuleState.DISCOVERED:
                    entry.state = ModuleState.VALIDATED
                continue
            if getattr(entry, "state", None) in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            ):
                continue
            entry.state = ModuleState.SKIPPED
            existing = self.records.get(name)
            if existing is None or getattr(existing, "state", None) not in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            ):
                self.records[name] = entry

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
                skip = ModuleSkip(
                    "cycle",
                    name,
                    "циклическая зависимость",
                )
                self.skipped.setdefault(name, [])
                if skip not in self.skipped[name]:
                    self.skipped[name].append(skip)
                entry = candidates[name]
                if getattr(entry, "state", None) in (
                    ModuleState.REGISTERED,
                    ModuleState.ACTIVE,
                ):
                    continue
                entry.state = ModuleState.SKIPPED
                existing = self.records.get(name)
                if existing is None or getattr(
                    existing,
                    "state",
                    None,
                ) not in (
                    ModuleState.REGISTERED,
                    ModuleState.ACTIVE,
                ):
                    self.records[name] = entry
        return ordered

    def _build_capabilities(self, candidates, log=None):
        """Собрать карту capability -> провайдер.

        Конфликт двух провайдеров не разрешается «первым победителем»:
        возможность исключается из карты, а конфликт логируется и
        сохраняется в ``capability_conflicts``.
        """
        capabilities = {}
        versions = {}
        conflicts = {}
        for name in sorted(
            candidates,
            key=lambda item: candidates[item].package.lower(),
        ):
            for capability, version in _capability_specs(
                candidates[name].manifest.provides
            ):
                key = str(capability)
                current = capabilities.get(key)
                if current is None:
                    capabilities[key] = name
                    versions[key] = str(version or "1.0.0")
                    continue
                if current == name:
                    continue
                providers = conflicts.setdefault(key, [current])
                if name not in providers:
                    providers.append(name)

        for key, providers in conflicts.items():
            capabilities.pop(key, None)
            versions.pop(key, None)
            if log is not None:
                log(
                    "Конфликт capability %s: модули %s. "
                    "Провайдер не выбран."
                    % (key, ", ".join(providers))
                )
        self.capability_conflicts = conflicts
        self.capability_versions = versions
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
                    self.capability_versions.pop(capability_name, None)
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
        optional_dependents = {name: set() for name in self.records}
        for name, record in self.records.items():
            for dependency in getattr(record, "dependencies", ()) or ():
                if dependency in dependents:
                    dependents[dependency].add(name)
            for dependency in getattr(
                record,
                "optional_dependencies",
                (),
            ) or ():
                if dependency in optional_dependents:
                    optional_dependents[dependency].add(name)
        for name, record in self.records.items():
            record.dependents = tuple(sorted(dependents.get(name, ())))
            record.optional_dependents = tuple(
                sorted(optional_dependents.get(name, ()))
            )
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

        if record.state is ModuleState.FAILED:
            return False
        if record.instance is None:
            self._record_failure(
                record,
                "import",
                "экземпляр модуля не импортирован",
                log,
            )
            return False

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
            self.skipped[name] = list(missing)
            log(
                "Модуль %s: не загружены зависимости: %s"
                % (
                    name,
                    ", ".join(skip.target for skip in missing),
                )
            )
            return False

        try:
            ctx = self.make_context(record.manifest)
        except Exception as exc:
            self._record_failure(
                record,
                "init",
                "ошибка создания контекста: %s" % exc,
                log,
                traceback.format_exc(),
            )
            return False

        record.ctx = ctx
        capabilities = ModuleCapabilities(
            self.capability_registry,
            record.manifest.id,
            record.manifest.provides,
            log,
        )
        record.capabilities = capabilities
        if ctx is not None:
            try:
                ctx.capabilities = capabilities
                ctx.kernel.capabilities = capabilities
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

        phase = "init"
        try:
            record.instance.init(ctx)
            record.state = ModuleState.INITIALIZED
            phase = "register"
            capabilities.begin_register()
            record.instance.register(ctx)
            capabilities.end_register()
            record.state = ModuleState.REGISTERED
        except Exception as exc:
            record.state = ModuleState.FAILED
            record.phase = phase
            record.error = "%s: %s" % (type(exc).__name__, exc)
            self.records[name] = record
            log(
                "Модуль %s: FAILED (фаза %s).\n%s"
                % (name, phase, traceback.format_exc())
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
                record.manifest.id,
                record.manifest.version,
                record.manifest.sdk_min,
            )
        )
        return True

    def _required_dependencies(self, record):
        names = set()
        for value in normalize_requirements(record.manifest.requires):
            names.add(value.id)
        for capability, _min_version in _capability_requirements(
            record.manifest.needs
        ):
            provider = self.capabilities.get(capability)
            if provider and provider != record.name:
                names.add(provider)
        return tuple(sorted(names))

    def _missing_dependencies(self, record):
        missing = []
        for value in normalize_requirements(record.manifest.requires):
            provider = self.records.get(value.id)
            if provider is None or provider.state not in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            ):
                missing.append(
                    ModuleSkip(
                        "missing_required_module",
                        value.id,
                        "зависимость не загружена",
                    )
                )
                continue
            if value.min_version and not version_at_least(
                provider.manifest.version,
                value.min_version,
            ):
                missing.append(
                    ModuleSkip(
                        "missing_required_module",
                        value.id,
                        "нужен >= %s, найден %s"
                        % (value.min_version, provider.manifest.version),
                    )
                )
        for capability, min_version in _capability_requirements(
            record.manifest.needs
        ):
            provider = self.capabilities.get(capability)
            if provider is None:
                kind = (
                    "capability_conflict"
                    if capability in self.capability_conflicts
                    else "missing_capability"
                )
                missing.append(
                    ModuleSkip(
                        kind,
                        capability,
                        "возможность не предоставлена",
                    )
                )
                continue
            if not _capability_version_ok(
                self.capability_versions.get(capability, ""),
                min_version,
            ):
                missing.append(
                    ModuleSkip(
                        "capability_version",
                        capability,
                        "нужна версия >= %s" % min_version,
                    )
                )
                continue
            if not self._dependency_ready(
                ModuleRequirement(provider)
            ):
                missing.append(
                    ModuleSkip(
                        "dependency_skipped",
                        provider,
                        "провайдер не зарегистрирован",
                    )
                )
        return tuple(missing)

    def _dependency_ready(self, requirement):
        record = self.records.get(requirement.id)
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
                % (entry.manifest.id, traceback.format_exc())
            )
        finally:
            scope = getattr(ctx, "registrations", None)
            if scope is not None:
                try:
                    removed = scope.dispose()
                except Exception:
                    log(
                        "Модуль %s: ошибка очистки регистраций.\n%s"
                        % (entry.manifest.id, traceback.format_exc())
                    )
                else:
                    if removed:
                        log(
                            "Модуль %s: снято регистраций %d"
                            % (entry.manifest.id, removed)
                        )
            self._release_runtime(entry, ctx, log)
            capabilities = getattr(entry, "capabilities", None)
            if capabilities is not None:
                try:
                    capabilities.close()
                except Exception:
                    log(
                        "Модуль %s: ошибка закрытия capabilities.\n%s"
                        % (entry.manifest.id, traceback.format_exc())
                    )
            removed_caps = self.capability_registry.remove_owner(
                entry.manifest.id
            )
            if removed_caps:
                log(
                    "Модуль %s: снято capability %d"
                    % (entry.manifest.id, removed_caps)
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
                    % (entry.manifest.id, traceback.format_exc())
                )

        tasks = getattr(ctx, "tasks", None)
        cancel = getattr(tasks, "cancel_owner", None)
        if callable(cancel):
            try:
                active = cancel()
            except Exception:
                active = 0
                log(
                    "Модуль %s: ошибка отмены задач.\n%s"
                    % (entry.manifest.id, traceback.format_exc())
                )
            if active:
                log(
                    "Модуль %s: отменено фоновых задач: %d; deactivate "
                    "не блокирует UI ожиданием callbacks"
                    % (entry.manifest.id, int(active or 0))
                )

    def _rollback(self, entry, ctx, log, state=ModuleState.FAILED):
        """Отозвать всё, что модуль успел создать до ошибки."""
        self._deactivate_entry(entry, ctx, log)
        entry.state = state
        self._drop_order(entry)
        self._rebuild_capabilities(log)
        self._unload_package(entry, log)

    def _drop_order(self, record):
        if record.name in self._order:
            self._order.remove(record.name)

    def _records(self):
        return list(self.records.values())

    def describe(self):
        """Снимок состояний модулей для диагностики."""
        result = {}
        records = dict(self._candidates)
        records.update(self.records)
        for name, record in records.items():
            result[name] = {
                "state": getattr(
                    record,
                    "state",
                    ModuleState.DISCOVERED,
                ).value,
                "phase": getattr(record, "phase", ""),
                "error": getattr(record, "error", ""),
                "skipped": tuple(
                    skip.kind for skip in self.skipped.get(name, ())
                ),
            }
        for name, skips in self.skipped.items():
            if name in result:
                continue
            result[name] = {
                "state": ModuleState.SKIPPED.value,
                "phase": "",
                "error": "",
                "skipped": tuple(skip.kind for skip in skips),
            }
        return result

    # -------------------------------------------------- фазы
    def activate_all(self, log=None):
        log = log or (lambda m: None)
        self._running = True
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
                self.records[name] = record
                self.skipped[name] = [
                    ModuleSkip(
                        "dependency_skipped",
                        dependency,
                        "зависимость не активирована",
                    )
                    for dependency in missing
                ]
                log(
                    "Модуль %s: зависимости не активированы: %s"
                    % (name, ", ".join(missing))
                )
                self._rollback(
                    record,
                    record.ctx,
                    log,
                    state=ModuleState.SKIPPED,
                )
                continue

            try:
                record.instance.activate(record.ctx)
                record.state = ModuleState.ACTIVE
            except Exception as exc:
                record.state = ModuleState.FAILED
                record.phase = "activate"
                record.error = "%s: %s" % (type(exc).__name__, exc)
                self.records[name] = record
                log(
                    "Модуль %s: FAILED (фаза activate).\n%s"
                    % (name, traceback.format_exc())
                )
                self._rollback(record, record.ctx, log)
        self._compute_dependents()

    def deactivate_all(self, log=None):
        log = log or (lambda m: None)
        self._running = False
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
        """Выгрузить модуль; при ``force`` сначала деактивируются зависимые."""
        log = log or (lambda m: None)
        record = self.records.get(str(name))
        if record is None:
            return False
        if record.name in self.required_ids:
            log(
                "Модуль %s: обязательный модуль нельзя выгрузить "
                "(ADR-35); доступна только безопасная остановка."
                % record.name
            )
            return False
        if record.state is ModuleState.UNLOADED:
            log("Модуль %s: уже выгружен." % record.name)
            return True

        dependents = self._active_dependents(record)
        optional = [
            name
            for name in self._active_optional_dependents(record)
            if name not in set(dependents)
        ]
        if (dependents or optional) and not force:
            blocked = list(dependents) + list(optional)
            log(
                "Модуль %s: нельзя выгрузить, зависят: %s"
                % (record.name, ", ".join(blocked))
            )
            return False
        optional_names = []
        if force:
            self._cascade_dependents(record, dependents, log)
            optional_names = self._deactivate_optional_dependents(
                record,
                optional,
                log,
            )

        if record.state in (
            ModuleState.ACTIVE,
            ModuleState.REGISTERED,
            ModuleState.INITIALIZED,
        ):
            self._deactivate_entry(record, record.ctx, log)

        record.state = ModuleState.UNLOADED
        record.instance = None
        record.ctx = None
        self._drop_order(record)
        self._candidates.pop(record.name, None)
        self.skipped.pop(record.name, None)
        self._unload_package(record, log)
        self._rebuild_capabilities(log)
        if optional_names:
            self._reactivate_optional_dependents(optional_names, log)
        self._compute_dependents()
        self._refresh_contexts()
        log("Модуль выгружен: %s" % record.name)
        return True

    def _active_dependents(self, record):
        """Транзитивные обязательные зависимые, которые активны."""
        result = []
        queue = list(record.dependents or ())
        seen = set()
        while queue:
            name = queue.pop(0)
            if name in seen:
                continue
            seen.add(name)
            dependent = self.records.get(name)
            if dependent is None:
                continue
            if dependent.state in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            ):
                result.append(name)
            queue.extend(dependent.dependents or ())
        return result

    def _active_optional_dependents(self, record):
        """Optional-зависимые, которые сейчас зарегистрированы/активны."""
        result = []
        queue = list(record.optional_dependents or ())
        seen = set()
        while queue:
            name = queue.pop(0)
            if name in seen:
                continue
            seen.add(name)
            dependent = self.records.get(name)
            if dependent is None:
                continue
            if dependent.state in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            ):
                result.append(name)
            queue.extend(dependent.optional_dependents or ())
        return result

    def _cascade_dependents(self, provider, dependents, log):
        """Деактивировать обязательных зависимых при выгрузке провайдера."""
        for name in reversed(dependents):
            record = self.records.get(name)
            if record is None:
                continue
            self._skip_dependent(record, provider, log)
        self._rebuild_capabilities(log)
        self._compute_dependents()

    def _skip_dependent(self, record, target, log):
        self._deactivate_entry(record, record.ctx, log)
        record.state = ModuleState.SKIPPED
        record.phase = ""
        record.error = ""
        record.instance = None
        record.ctx = None
        self._drop_order(record)
        self.skipped[record.name] = [
            ModuleSkip(
                "provider_unloaded",
                target.name,
                "провайдер выгружен или отключён",
            )
        ]
        self._unload_package(record, log)
        log(
            "Модуль %s: SKIPPED (provider_unloaded: %s)"
            % (record.name, target.name)
        )

    def _deactivate_optional_dependents(self, provider, names, log):
        """Снять optional-зависимых, чтобы после выгрузки включить degraded."""
        result = []
        for name in names:
            record = self.records.get(name)
            if record is None or record.state not in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            ):
                continue
            required_closure = self._active_dependents(record)
            self._cascade_dependents(record, required_closure, log)
            self._skip_dependent(record, provider, log)
            result.append(name)
        self._rebuild_capabilities(log)
        self._compute_dependents()
        return result

    def _reactivate_optional_dependents(self, names, log):
        """Повторно включить optional-зависимых без выгруженного провайдера."""
        for name in names:
            record = self.records.get(name)
            if record is None or record.state is not ModuleState.SKIPPED:
                continue
            entry = self._read_entry(record.package, log)
            if entry is None:
                continue
            if self._admission_errors(entry):
                continue
            entry.state = ModuleState.VALIDATED
            if not self._import_entry(entry, log):
                continue
            if not self._start_module(entry, {}, log):
                continue
            self.skipped.pop(name, None)
            self._restore_provider_dependents(name, log)
        if self._running:
            self.activate_all(log)

    def reload(self, name, log=None):
        """Выгрузить и снова загрузить модуль без перезапуска приложения."""
        log = log or (lambda m: None)
        record = self.records.get(str(name))
        if record is None:
            return False
        if record.name in self.required_ids:
            log(
                "Модуль %s: обязательный модуль нельзя перезагрузить "
                "(ADR-35)." % record.name
            )
            return False

        dependents = self._active_dependents(record)
        if dependents:
            log(
                "Модуль %s: нельзя перезагрузить, зависят: %s"
                % (record.name, ", ".join(dependents))
            )
            return False
        optional = [
            name
            for name in self._active_optional_dependents(record)
            if name not in set(dependents)
        ]

        was_active = record.state is ModuleState.ACTIVE
        package = record.package
        if not self.unload(name, log=log, force=bool(optional)):
            return False

        entry = self._read_entry(package, log)
        if entry is None:
            return False
        errors = self._admission_errors(entry)
        if errors:
            entry.state = ModuleState.SKIPPED
            self.records[entry.name] = entry
            self.skipped[entry.name] = list(errors)
            self._rebuild_capabilities(log)
            self._compute_dependents()
            log(
                "Модуль %s: не допущен после reload: %s"
                % (
                    entry.name,
                    ", ".join(skip.kind for skip in errors),
                )
            )
            return False
        entry.state = ModuleState.VALIDATED
        if not self._import_entry(entry, log):
            return False
        if not self._start_module(entry, {}, log):
            return False
        if was_active:
            self.activate_all(log)
        self._restore_provider_dependents(entry.name, log)
        self._restore_optional_dependents(entry.name, log)
        self._compute_dependents()
        self._refresh_contexts()
        loaded = self.records.get(entry.name)
        return loaded is not None and loaded.state in (
            ModuleState.REGISTERED,
            ModuleState.ACTIVE,
        )

    def _admission_errors(self, entry):
        """Проверить прямые зависимости нового entry до импорта кода."""
        errors = []
        for capability, _version in _capability_specs(
            entry.manifest.provides
        ):
            owner = self.capabilities.get(capability)
            if owner is not None and owner != entry.name:
                errors.append(
                    ModuleSkip(
                        "capability_conflict",
                        capability,
                        "capability уже предоставлена модулем %s" % owner,
                    )
                )
        for value in normalize_requirements(entry.manifest.requires):
            provider = self.records.get(value.id)
            if provider is None or provider.state not in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            ):
                errors.append(
                    ModuleSkip(
                        "missing_required_module",
                        value.id,
                        "зависимость не загружена",
                    )
                )
                continue
            if value.min_version and not version_at_least(
                provider.manifest.version,
                value.min_version,
            ):
                errors.append(
                    ModuleSkip(
                        "missing_required_module",
                        value.id,
                        "нужен >= %s, найден %s"
                        % (value.min_version, provider.manifest.version),
                    )
                )
        for capability, min_version in _capability_requirements(
            entry.manifest.needs
        ):
            provider = self.capabilities.get(capability)
            if provider is None:
                kind = (
                    "capability_conflict"
                    if capability in self.capability_conflicts
                    else "missing_capability"
                )
                errors.append(
                    ModuleSkip(
                        kind,
                        capability,
                        "возможность недоступна",
                    )
                )
                continue
            if not _capability_version_ok(
                self.capability_versions.get(capability, ""),
                min_version,
            ):
                errors.append(
                    ModuleSkip(
                        "capability_version",
                        capability,
                        "нужна версия >= %s" % min_version,
                    )
                )
                continue
            provider_record = self.records.get(provider)
            if provider_record is None or provider_record.state not in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            ):
                errors.append(
                    ModuleSkip(
                        "dependency_skipped",
                        provider,
                        "провайдер не активирован",
                    )
                )
        return errors

    def set_enabled(self, module_id, enabled, log=None):
        """Явно включить/отключить модуль и сохранить намерение."""
        log = log or (lambda m: None)
        module_id = str(module_id or "")
        if self.config is None:
            log(
                "config/modules.json недоступен: "
                "включение/отключение невозможно"
            )
            return False
        if not enabled and module_id in self.required_ids:
            log(
                "Модуль %s: обязательный модуль нельзя отключить "
                "(ADR-35)." % module_id
            )
            return False
        if not self.config.set_enabled(module_id, enabled):
            return False

        record = self.records.get(module_id)
        if not enabled:
            if record is not None and record.state not in (
                ModuleState.DISABLED,
                ModuleState.UNLOADED,
            ):
                self.unload(module_id, log=log, force=True)
            record = self.records.get(module_id)
            if record is not None:
                record.state = ModuleState.DISABLED
                record.instance = None
                record.ctx = None
                record.phase = ""
                record.error = ""
                self.skipped.pop(module_id, None)
            self._rebuild_capabilities(log)
            self._compute_dependents()
            self._refresh_contexts()
            log("Модуль %s: DISABLED." % module_id)
            return True

        if record is not None and record.state in (
            ModuleState.REGISTERED,
            ModuleState.ACTIVE,
        ):
            return True
        package = record.package if record is not None else module_id
        entry = self._read_entry(package, log)
        if entry is None:
            return False
        errors = self._admission_errors(entry)
        if errors:
            entry.state = ModuleState.SKIPPED
            self.records[entry.name] = entry
            self.skipped[entry.name] = list(errors)
            self._rebuild_capabilities(log)
            self._compute_dependents()
            log(
                "Модуль %s: не допущен: %s"
                % (entry.name, ", ".join(skip.kind for skip in errors))
            )
            return False
        entry.state = ModuleState.VALIDATED
        if not self._import_entry(entry, log):
            return False
        if not self._start_module(entry, {}, log):
            return False
        if self._running:
            self.activate_all(log)
        self._restore_provider_dependents(module_id, log)
        self._restore_optional_dependents(module_id, log)
        self._compute_dependents()
        self._refresh_contexts()
        loaded = self.records.get(entry.name)
        return loaded is not None and loaded.state in (
            ModuleState.REGISTERED,
            ModuleState.ACTIVE,
        )

    def _restore_provider_dependents(self, provider_id, log):
        """Попытаться вернуть зависимых, снятых с провайдером."""
        pending = [
            name
            for name, record in self.records.items()
            if record.state in (
                ModuleState.SKIPPED,
                ModuleState.FAILED,
            )
            and any(
                skip.kind == "provider_unloaded"
                and skip.target == provider_id
                for skip in self.skipped.get(name, ())
            )
        ]
        if not pending:
            return
        total = len(pending)
        while pending:
            progress = False
            for name in list(pending):
                record = self.records.get(name)
                if record is None:
                    pending.remove(name)
                    continue
                entry = self._read_entry(record.package, log)
                if entry is None:
                    pending.remove(name)
                    continue
                if self._admission_errors(entry):
                    continue
                entry.state = ModuleState.VALIDATED
                if not self._import_entry(entry, log):
                    pending.remove(name)
                    continue
                if not self._start_module(entry, {}, log):
                    pending.remove(name)
                    continue
                self.skipped.pop(name, None)
                pending.remove(name)
                self._restore_provider_dependents(name, log)
                progress = True
            if not progress:
                break
        if self._running:
            self.activate_all(log)
        log(
            "Модуль %s: восстановлено зависимых %d"
            % (provider_id, total - len(pending))
        )

    def _restore_optional_dependents(self, provider_id, log):
        """Переинициализировать активных degraded optional-зависимых."""
        provider = self.records.get(provider_id)
        if provider is None:
            return
        for name in list(self._active_optional_dependents(provider)):
            record = self.records.get(name)
            if record is None or record.state not in (
                ModuleState.REGISTERED,
                ModuleState.ACTIVE,
            ):
                continue
            self._reinit_module(record, log)
        self._compute_dependents()
        self._refresh_contexts()

    def _reinit_module(self, record, log):
        """Перезапустить модуль, не трогая его optional-зависимых."""
        required = self._active_dependents(record)
        self._cascade_dependents(record, required, log)
        self._deactivate_entry(record, record.ctx, log)
        record.state = ModuleState.UNLOADED
        record.instance = None
        record.ctx = None
        self._drop_order(record)
        self._unload_package(record, log)
        entry = self._read_entry(record.package, log)
        if entry is None:
            return False
        errors = self._admission_errors(entry)
        if errors:
            entry.state = ModuleState.SKIPPED
            self.records[entry.name] = entry
            self.skipped[entry.name] = list(errors)
            self._rebuild_capabilities(log)
            return False
        entry.state = ModuleState.VALIDATED
        if not self._import_entry(entry, log):
            return False
        if not self._start_module(entry, {}, log):
            return False
        self.skipped.pop(entry.name, None)
        if self._running:
            self.activate_all(log)
        self._restore_provider_dependents(entry.name, log)
        return True

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
                % (record.manifest.id, len(removed))
            )


def _capability_values(values):
    if isinstance(values, str):
        return (values,)
    result = []
    for value in values or ():
        name = getattr(value, "name", None)
        if name is None:
            if isinstance(value, (tuple, list)) and value:
                name = value[0]
            else:
                name = value
        result.append(str(name))
    return tuple(result)


def _capability_requirements(values):
    """Вернуть пары (name, min_version) из манифеста."""
    if isinstance(values, str):
        values = (values,)
    result = []
    for value in values or ():
        name = getattr(value, "name", None)
        min_version = getattr(value, "min_version", None)
        if name is None:
            if isinstance(value, (tuple, list)) and value:
                name = str(value[0])
                min_version = str(value[1]) if len(value) > 1 else ""
            else:
                name = str(value)
                min_version = ""
        result.append((str(name), str(min_version or "")))
    return tuple(result)


def _capability_version_ok(provider_version, min_version):
    """Совместимость capability: major совпадает и версия не ниже."""
    return capability_version_ok(provider_version, min_version)


def _capability_specs(values):
    """Вернуть пары (name, version) из манифеста."""
    if isinstance(values, str):
        values = (values,)
    result = []
    for value in values or ():
        name = getattr(value, "name", None)
        version = getattr(value, "version", None)
        if name is None:
            if isinstance(value, (tuple, list)) and value:
                name = str(value[0])
                version = str(value[1]) if len(value) > 1 else "1.0.0"
            else:
                name = str(value)
                version = "1.0.0"
        result.append((str(name), str(version or "1.0.0")))
    return tuple(result)


def _current_platform():
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    return sys.platform

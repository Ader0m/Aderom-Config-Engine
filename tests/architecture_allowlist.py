# -*- coding: utf-8 -*-
"""Allowlist публичных имён и временных архитектурных исключений.

Файл фиксирует состояние на Этапе 0. Любое расширение списков
требует решения оператора и ADR. Списки не являются разрешением
добавлять новые нарушения: они только делают текущие видимыми.
"""

SDK_PUBLIC_NAMES = frozenset({
    'ApiLevel',
    'AppPaths',
    'CapabilitiesApi',
    'Capability',
    'CapabilityConflictError',
    'CapabilityError',
    'CapabilityUnavailable',
    'CapabilityVersionError',
    'CommandApi',
    'CoreApi',
    'DEFAULT_SEARCH_DELAY_MS',
    'DockSpec',
    'EventApi',
    'Events',
    'FileOpenFailed',
    'FileOpenHandler',
    'FileOpened',
    'FileReader',
    'ForestRef',
    'GraphEdgeStyle',
    'GraphMatch',
    'GraphNode',
    'HintContext',
    'HintItem',
    'HintProvider',
    'HintRegistration',
    'HintResult',
    'HintTargetInfo',
    'HintsHandle',
    'KIND_COLOR',
    'KIND_DIRECTIVE',
    'KIND_INCLUDE',
    'KIND_KEY',
    'KIND_LOCALIZATION',
    'KIND_SECTION',
    'KIND_TEXT',
    'KernelApi',
    'LANGUAGE_LOCALIZATION_TEXT',
    'LANGUAGE_LTX',
    'LANGUAGE_PLAIN',
    'LANGUAGE_XML',
    'MATCH_CONTAINS',
    'MATCH_FUZZY',
    'MATCH_PREFIX',
    'ModuleBase',
    'ModuleContext',
    'ModuleManifest',
    'ModuleRequirement',
    'MODULE_MANIFEST_VERSION',
    'MissingCapability',
    'NullHintsHandle',
    'ProjectOverlay',
    'RegistrationHandle',
    'RegistrationScope',
    'SDK_VERSION',
    'SURFACE_ENGINE_TEXT',
    'SURFACE_PLAIN',
    'SURFACE_RAW',
    'SURFACE_RESOLVED',
    'SectionGraph',
    'SectionGraphContribution',
    'SectionGraphRequest',
    'SnapshotChanged',
    'TabSpec',
    'TaskApi',
    'TreeReady',
    'TreeSummary',
    'UiApi',
    'WindowSpec',
    'api_level',
    'is_compatible',
    'is_stable',
    'normalize_requirement',
    'normalize_requirements',
    'parse_version',
    'version_at_least',
})

CORE_PUBLIC_NAMES = frozenset()
# Корень ``core`` больше не реэкспортирует внутренности: публичные порты
# живут в ``core.api.*``. Список намеренно пуст (Этап 9).

# UI -> core: legacy-исключения закрыты. UI получает данные через
# app-порты, а не через core.
UI_CORE_IMPORT_ALLOWLIST = frozenset()

# app -> sdk: только контрактные модули. Адаптеры и внутренности
# запрещены; список должен уменьшаться по мере Этапа 1.
APP_SDK_IMPORT_ALLOWLIST = frozenset({
    "sdk.contracts",
    "sdk.contracts.events",
    "sdk.contracts.forest",
    "sdk.contracts.paths",
    "sdk.features.documents",
    "sdk.features.items",
    "sdk.features.items.models",
    "sdk.features.projects",
    "sdk.ui",
})

# Импорт пакета modules разрешён только загрузчику.
MODULES_PACKAGE_IMPORTERS = frozenset({
    "ui/module_loader.py",
})

# Composition root создаёт QApplication; виджеты остаются в ui/modules.
QT_WIDGETS_COMPOSITION_ROOTS = frozenset({
    "main.py",
})

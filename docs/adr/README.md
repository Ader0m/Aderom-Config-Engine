# ADR — архитектурные решения

Каталог для Architecture Decision Records.

Правило: любое отступление от `AGENTS.md` или целевой архитектуры
оформляется ADR. ADR не отменяет запрет автоматически — он фиксирует
причину, срок и план устранения.

## Шаблон

Обязательный шаблон — [`0000-template.md`](0000-template.md):

```text
# ADR-NNNN. Краткое название

Дата:
Статус: proposed | accepted | superseded | rejected

## Контекст
Что заставило принять решение.

## Решение
Что именно решено.

## Альтернативы
Что рассматривалось и почему отклонено.

## Последствия
Что усложняется, что упрощается, какие риски.

## Срок и условия выхода
Когда и как решение будет пересмотрено или удалено.
```

## Текущие ADR

Базовые решения (полные формулировки — в
`docs/Целевая архитектура приложения.md`, §10):

- ADR-1 — прикладной слой `app` — законный слой.
- ADR-2 — SDK как чистый контракт.
- ADR-3 — семантическая модель первична, текст — проекция.
- ADR-4 — один снимок и один контракт кэша.
- ADR-5 — запись только через авторизованную транзакцию.
- ADR-6 — окна и вкладки принадлежат платформе.
- ADR-7 — модули связываются возможностями, а не именами.
- ADR-8 — доменные фичи вне корня `core` и SDK.
- ADR-9 — производительность через снимки, кэш и ленивость.
- ADR-10 — Resolver4 как зафиксированный baseline.

Решения Этапа 0:

- [ADR-11](ADR-11-compatibility-policy.md) — политика совместимости до
  `1.0`.
- [ADR-12](ADR-12-patch-write-freeze.md) — запись патчей заморожена до
  Этапа 5.
- [ADR-13](ADR-13-architecture-guardrails.md) — архитектурные guardrails
  и allowlist.

Решения Этапа 1:

- [ADR-14](ADR-14-sdk-clean-contract.md) — чистый контракт SDK,
  capabilities и порты ядра.

Решения Этапа 2:

- [ADR-15](ADR-15-snapshot-cache.md) — снимок VFS, единый кэш и
  идентичность путей.

Решения Этапа 3:

- [ADR-16](ADR-16-project-schema-migration.md) — схема проекта,
  `ProjectRepository` и one-time migration.
- [ADR-17](ADR-17-explicit-serialized-commands.md) — явные
  сериализованные команды как single-writer на Этапе 3; очередь
  команд — Этап 6.

Решения Этапа 4:

- [ADR-18](ADR-18-semantic-model.md) — семантическая модель LTX,
  происхождение значений, ленивый `SectionQueryService` и read-only
  карточка секции.
- [ADR-19](ADR-19-semantic-parity.md) — воспроизводимый дамп секций,
  реестр известных отличий и статус 19023/19024.

Решения Этапа 5:

- [ADR-20](ADR-20-patch-engine-contract.md) — контракт патч-движка,
  оркестратор, форматные движки и переименование SDK-запроса
  `PatchIntent` → `PatchRequest`.
- [ADR-21](ADR-21-preview-contributors.md) — предпросмотр asset/new-file
  через движки и перенос XML-слияния в `core`; закрытие CR-5/DATA-5.
- [ADR-22](ADR-22-commit-authorization.md) — `PatchCommitService`,
  `CommitAuthorization`, единственная точка записи и закрытие
  CR-1/CR-2.
- [ADR-23](ADR-23-full-roundtrip.md) — полный diff затронутого LTX-корня,
  проверка источника, дублей и `DLTX_DELETE` перед авторизацией.
- [ADR-24](ADR-24-async-patch-write-ui.md) — фоновая запись через
  `TaskService`, прогресс/отмена и UI предпросмотра с diff и рисками.

Решения Этапа 6:

- [ADR-25](ADR-25-task-operation-contract.md) — `operation_key`,
  `title` и `depends_on` в `TaskApi.run`; `OperationRegistry` и
  `TaskHandle` как платформенная инфраструктура.
- [ADR-26](ADR-26-window-specs.md) — `WindowSpec`/`DockSpec`/`TabSpec`,
  `open_window`/`open_tab`/`open_dock` и `save_state`/`load_state`.
- [ADR-27](ADR-27-file-open-context.md) — контексты открытия
  `view/edit/preview/patch`, предпочтения и разрешение неоднозначности.
- [ADR-28](ADR-28-file-open-handler-id.md) — стабильный `handler_id`
  обработчика открытия и ключ предпочтения.
- [ADR-29](ADR-29-task-pool.md) — пул потоков `TaskService`,
  `max_workers`, приоритеты и отмена через `current_task()`.
- [ADR-30](ADR-30-theme-changed-event.md) — событие
  `Events.UI_THEME_CHANGED` для перекраски открытых окон и вкладок
  модулей без пересоздания.

Решения перед Этапом 7 (приняты до кода):

- [ADR-31](ADR-31-manifest-and-lifecycle.md) — `module.json`, состояния
  загрузчика, `config/modules.json`, trust, reload/unload и изоляция
  ошибок.
- [ADR-32](ADR-32-capabilities-and-public-sdk.md) — типизированные
  capability-протоколы, версии, конфликт провайдеров как ошибка
  валидации, `CapabilitiesApi` вместо публичных registry/scope,
  `ModuleManifest.id` и SDK `0.12.0` (позже повышен до `0.13.0`
  шагом 7.10, ADR-36).
- [ADR-33](ADR-33-base-item-browser-domain.md) — `item_browser`, домен
  items/ogg, default-set и закрытие MOD-1…MOD-4; в части `base`/`ogg_editor`
  заменён ADR-35.
- [ADR-34](ADR-34-module-data-and-settings.md) - настройки модулей,
  `config/modules.json`, `layout.json`, перенос DATA-4 на Этап 9 и
  закрытие P1 `needs_confirmation`; DATA-4 закрыт в аудите 25.11.2026.
- [ADR-35](ADR-35-base-required-module.md) — `base` как обязательный
  встроенный модуль, `ogg_editor.requires base` и отмена критерия
  запуска без `base`.

Решение шага 7.10:

- [ADR-36](ADR-36-graph-confidence.md) — `confidence` связей графа,
  правила `exact/probable/heuristic`, отображение в UI и SDK `0.13.0`.

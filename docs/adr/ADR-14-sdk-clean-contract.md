# ADR-14. Чистый контракт SDK и порты ядра

Дата: 22.11.2026
Статус: accepted

## Контекст

Этап 1 требует сделать SDK стабильным и маленьким: убрать доменные
понятия из корня, ввести capabilities и типизированный контекст. До
изменения `sdk/__init__.py` экспортировал `ItemCatalog`, `ItemEntry`,
resolved-модели и реализации адаптеров. `ModuleContext` сам создавал
адаптеры и искал методы платформы по именам. `app` импортировал `core`
целиком, а `core/__init__.py` оставался широкой публичной свалкой.

Оператор разрешил breaking change строго по плану Этапа 1 с обновлением
встроенных модулей и тестов в том же изменении.

## Решение

1. Разделить SDK на пакеты:
   - `sdk/contracts/` — общие DTO, события, пути, read-only лес;
   - `sdk/kernel/` — модуль, контекст, регистрации, версия, capabilities,
     базовые протоколы сервисов, задач, событий и команд;
   - `sdk/ui/` — протокол UI, обработчики файлов и подсказки;
   - `sdk/features/items/` — DTO и протокол возможностей предметов;
   - `sdk/features/documents/` — документы, resolved-модели и текст движка;
   - `sdk/features/projects/` — настройки, asset-типы и читатели файлов;
   - `sdk/features/patches/` — предложение патча без фиксации.
2. Оставить `sdk/__init__.py` тонким фасадом. Корень SDK не экспортирует
   `ItemCatalog`, `ItemEntry`, `ItemSection`, `LocalizationString`,
   resolved-модели, project-DTO и реализации адаптеров.
3. Вынести адаптеры в `ui/platform/sdk_bridge/`. `sdk` не импортирует
   `core`, `app`, `platform`, `ui` и `modules`. Адаптеры не ищут методы
   платформы или сервисов по строковым именам: контракт должен быть
   явным.
4. Ввести `CapabilityRegistry`/`CapabilityScope`:
   - `provide(name, version, obj)`;
   - `require(name, min_version)`;
   - `optional(name, min_version)`;
   - версия, владелец и удаление по владельцу;
   - одна capability принадлежит одному владельцу: `declare`/`provide`
     не заменяют чужую capability, конфликт провайдеров логируется и
     capability исключается из выбора;
   - `ModuleManifest.sdk_max` проверяется вместе с `sdk_min`;
   - при `FAILED`/`UNLOADED` владелец capability очищается.
5. Ввести `ctx.kernel`, `ctx.documents`, `ctx.patches`, `ctx.projects`,
   `ctx.items` и `ctx.module_id`. `ModuleContext` не создаёт адаптеры и
   не хранит необъявленные поля.
6. `PatchApi.propose` остаётся контрактом предложения. До Этапа 5
   `PatchApiAdapter` явно сообщает, что движок недоступен, и ничего не
   пишет. `CommitAuthorization` и `confirmed=True` не создаются.
7. Ввести уровни API `stable`/`experimental`/`internal` и поднять
   `SDK_VERSION` до `0.3.0`.
8. Добавить явные порты `core/api/{vfs,documents,resolve,graph,patches}.py`
   и перевести `app` на `core.api.*`. `CoreServices` остаётся переходным
   композиционным адаптером до Этапа 9.
9. Удалить мёртвый `src/old/*` (MOD-5).

## Альтернативы

1. **Сохранить старые имена как shim/deprecation.** Отклонено: политика
   до `1.0` запрещает shim'ы и параллельные имена.
2. **Оставить адаптеры в SDK.** Отклонено: SDK начал бы знать о форме
   внутренних сервисов и продолжил бы искать методы по именам.
3. **Оставить item/resolved-модели в корне SDK.** Отклонено: корень
   снова стал бы доменной свалкой.
4. **Сделать полноценный `CoreServices`-порт сразу.** Отклонено в рамках
   этого checkpoint: сначала фиксируются порты и направление зависимостей,
   полное разбиение фасада — Этап 9.

## Последствия

- Старые импорты `sdk.api`, `sdk.adapters`, `sdk.events`, `sdk.forest`,
  `sdk.paths`, `sdk.hints`, `sdk.engine_text` удалены. Встроенные модули,
  UI, app и тесты переведены на новые пути в том же изменении.
- Корень SDK стал меньше; allowlist `sdk.__all__` обновлён.
- `app` больше не импортирует `core` напрямую и зависит только от
  `core.api.*`.
- `core/facade.py` и `core/__init__.py` остаются переходными: их
  публичная поверхность сужается на Этапе 9. На момент ADR
  `CoreServices` был доступен через порт `core.api.services`; в аудите
  25.11.2026 порт удалён, см. обновление ниже.
- `ctx.items`/`ctx.projects`/`ctx.documents` — переходные feature-API.
  Доменный `items.catalog` как capability и вынос в модуль закрываются
  на Этапе 7.
- `ogg_editor.requires=("base",)` не меняется; по ADR-35 это намеренная
  зависимость от обязательного `base`.
- Легаси-импорты `ui → core` остаются в карантине до Этапов 3/6.

## Срок и условия выхода

- Этап 5: `PatchApi.propose` получает реальный движок, фиксация остаётся
  только через авторизованную транзакцию.
- Этап 7: item-домен и capability `items.catalog` переезжают в модуль.
- Этап 9: `CoreServices` и `core/__init__.py` сужаются до явных портов,
  transitional-адаптеры удаляются.

### Обновление 25.11.2026

`SDK-3`/`SDK-4` закрыты: `core/__init__.py` пуст, `core/api/services.py`
удалён. `CoreServices` заменён внутренним `CoreComposition` в
`core/composition.py`; composition root импортирует его напрямую, а
`app`/`ui`/`modules`/`sdk` — нет.

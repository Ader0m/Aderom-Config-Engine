# ADR-32. Capabilities и публичный SDK

Дата: 24.11.2026
Статус: accepted

## Контекст

`CapabilityRegistry`/`CapabilityScope` уже существуют, но модуль получает
capabilities через необязательный по типу контекст, а сами контракты
смешаны: `ItemApi` содержит и доменные запросы каталога, и UI-действие
открытия карточки; версии capability не имеют единого правила
совместимости; обязательные и необязательные возможности различаются
только наличием строки в манифесте; при двух провайдерах возможность
молча исключается; публичные ошибки capability не входят в корень SDK.
Нужно зафиксировать типизированный контракт до реализации Этапа 7.

## Решение

### 1. Где живут протоколы capability

Протоколы и DTO живут только в `sdk/features/<domain>/` и не импортируют
`core`, `app`, `platform`, `ui` или `modules`. Платформенные реализации
находятся в `app`/`ui/platform`, модульные — в провайдере capability.

Начальный набор:

| Capability | Версия | Протокол | Провайдер |
|---|---:|---|---|
| `items.catalog` | `1.0.0` | `sdk.features.items.ItemCatalogApi` | `item_browser` |
| `item.section.dragdrop` | `1.0.0` | `sdk.features.items.ItemSectionDropApi` | `item_browser` |
| `section.graph` | `1.0.0` | `sdk.features.graph.SectionGraphApi` | `base` |
| `project.assets` | `1.0.0` | `sdk.features.projects.ProjectAssetsApi` | платформа |
| `document.viewer.ltx` | `1.0.0` | `sdk.features.documents.DocumentViewerApi` | `base` |
| `document.viewer.xml` | `1.0.0` | `sdk.features.documents.DocumentViewerApi` | `base` |
| `audio.ogg.editor` | `1.0.0` | `sdk.features.ogg.OggEditorApi` | `ogg_editor` |

`ItemApi` разделяется на доменный `ItemCatalogApi` и UI/командные
операции модуля. В capability-протоколе не должно быть методов
открытия окон, команд меню и доступа к чужим виджетам.

### 2. `needs` и `optional_needs`

- `needs` — обязательная capability. Если провайдера нет, версия
  несовместима или провайдер конфликтует, модуль получает `SKIPPED` до
  `init` с причиной `missing_capability`, `capability_version` или
  `capability_conflict`.
- `optional_needs` — необязательная capability. `ctx.capabilities.optional`
  возвращает `None`; модуль обязан работать в деградированном режиме.
- Ссылку на capability модуль получает в `init`/`register` и хранит;
  повторный `require` на каждый кадр/строку запрещён.
- `requires`/`optional_requires` остаются зависимостями по id модуля и
  используются только для жизненного цикла/порядка загрузки. Для
  функциональной зависимости используется capability.

### 3. Версия capability

Провайдер объявляет `name` и `version` (semver). Потребитель объявляет
`name` и `min_version`. Совместимость:

```text
provider.major == requested.major
provider.version >= requested.min_version
```

Для Этапа 7 все встроенные capability имеют версию `1.0.0`; диапазон
`max_version` в манифесте не вводится, пока нет внешних модулей.
`min_version` обязателен в манифесте для `needs`/`optional_needs`.
Версия провайдера не может быть ниже `1.0.0`; версии `0.x` для
публичных capability не используются.

### 4. Два провайдера: lifecycle конфликта

Приоритетов и «первого победителя» нет. Конфликт — это ошибка
валидации, а не runtime-гонка:

1. `discover`/`validate` собирает **все** манифесты до импорта и
   активации любого модуля.
2. Если одну capability объявляют два манифеста, обе записи помечаются
   конфликтом. **Оба провайдера** получают `SKIPPED` с
   `capability_conflict`; ни один не переходит в `LOADED`.
3. Обязательные потребители конфликтующей capability получают
   `SKIPPED` с `capability_conflict`; необязательные — `None` и могут
   работать в degraded mode.
4. Если второй провайдер появляется при runtime enable/reload, новый
   провайдер отклоняется до `init` с `SKIPPED`/`capability_conflict`.
   Уже активный провайдер и его потребители остаются как были; тихая
   замена или деактивация работающего провайдера запрещена.
5. UI/диагностика показывает конфликт и оба id. Замена выполняется явно:
   отключить/выгрузить старый, затем включить новый.

Фактический `provide` обязан **точно** совпадать с манифестом:

- имя и версия не могут отличаться от `provides`;
- `provide` разрешён только в `register`; к концу `register` каждая
  объявленная capability должна быть предоставлена;
- отсутствующая, лишняя или несовпадающая capability переводит модуль в
  `FAILED` (фаза `register`), регистрации откатываются, capability не
  публикуется;
- потребители не инициализируются, пока провайдер не получил
  `REGISTERED`; при `FAILED` провайдера они получают `SKIPPED`
  `dependency_skipped`/`missing_capability`.

Автоматический выбор по приоритету или последнему загруженному запрещён.
Явный `provider_overrides` в конфигурации не входит в Этап 7; при
необходимости он оформляется отдельным ADR.

### 5. Выгрузка провайдера

- Обязательные зависимые модули деактивируются до выгрузки провайдера и
  получают `SKIPPED` с причиной `provider_unloaded`.
- Необязательные зависимые деактивируются и пытаются активироваться
  снова без отсутствующей capability.
- Если провайдер заменяется/перезагружается, ранее активные зависимые
  восстанавливаются только после успешной повторной валидации.
- Пользовательское отключение/выгрузка при активных зависимых требует
  подтверждения; закрытие приложения — без диалога, только безопасная
  остановка.

### 6. Публичный API

`ctx.capabilities` типизируется протоколом `CapabilitiesApi`:

```python
class CapabilitiesApi(Protocol):
    def provide(self, name: str, version: str, obj: object): ...
    def require(self, name: str, min_version: str = "1.0.0"): ...
    def optional(self, name: str, min_version: str = "1.0.0"): ...
    def has(self, name: str, min_version: str = "") -> bool: ...
    def version(self, name: str) -> str | None: ...
    def provider(self, name: str) -> str | None: ...
```

`provide` разрешён только в `register` владельца; к концу `register`
фактическое имя и версия должны совпадать с манифестом. Платформа
отклоняет provide после выгрузки и не даёт заменить чужую capability.

`CapabilitiesApi` — **единственный публичный контракт** для
`ctx.capabilities`. `CapabilityRegistry` и `CapabilityScope` остаются
внутренней реализацией платформенного загрузчика, удаляются из
`sdk.__all__`/`sdk.kernel.__all__` и не импортируются модулями.
Отдельного «advanced API» нет: два равноправных способа запрещены.

`ModuleManifest` получает `id` (identity) и `name` (display) по ADR-31;
`ModuleBase.manifest` удаляется; `ModuleRequirement.name` переименовывается
в `id` и означает только id модуля. Старые имена не сохраняются.

В `sdk.__all__` добавляются:

- `CapabilitiesApi`;
- `Capability`;
- `CapabilityError`, `MissingCapability`, `CapabilityVersionError`,
  `CapabilityUnavailable`, `CapabilityConflictError`;
- `MODULE_MANIFEST_VERSION` (значение `1`).

Из публичного контракта удаляются `CapabilityRegistry` и
`CapabilityScope`. `ModuleState`/`ModuleSkip` остаются внутренними типами
платформенного загрузчика и в SDK не экспортируются: модуль не должен
зависеть от состояний менеджера модулей.

Версия SDK при реализации Этапа 7 повышается до `0.12.0` по ADR-11.
Destructive renames выполняются без shim'ов: `ItemApi` не сохраняется
как алиас `ItemCatalogApi`, `ctx.capabilities` не получает
строковый fallback.

### 7. Conformance

Обязательные проверки:

1. Модуль-образец импортирует только `sdk`, PyQt для своих виджетов и
   собственные зависимости.
2. `provides`/`needs`/`optional_needs` валидируются до `init`.
3. Отсутствие required capability даёт `SKIPPED`, отсутствие optional —
   `None` и работоспособный degraded mode.
4. Конфликт двух провайдеров обнаруживается на валидации: оба
   провайдера `SKIPPED`, потребители `SKIPPED`/`None`; runtime-конфликт
   отклоняет только новый провайдер.
5. Фактический `provide` с другим именем/версией или отсутствующая
   объявленная capability дают `FAILED` фазы `register` и rollback.
6. Выгрузка провайдера каскадно обрабатывает зависимых.
7. Нет импортов `sdk.features.*` → `core/app/ui/modules`.
8. Нет class-manifest, строковых версий capability и shim-алиасов.
9. `sdk.__all__` совпадает с ADR-32 и ADR-31; `CapabilityRegistry` и
   `CapabilityScope` не экспортируются.

## Альтернативы

1. **Оставить `ItemApi` смешанным.** Отклонено: capability начинала бы
   знать UI и нарушала бы границу домена.
2. **Разрешать конфликт по приоритету.** Отклонено: скрытый выбор
   меняет поведение без согласия пользователя.
3. **При runtime-конфликте деактивировать уже работающего провайдера и
   его потребителей.** Отклонено: установка/включение нового модуля не
   должна ломать работающий сценарий; отклоняется только новый провайдер.
4. **Использовать только `min_version` без правила major.** Отклонено:
   `2.0.0` могла бы несовместимо заменить `1.x`.
5. **Вынести `ModuleState` в SDK.** Отклонено: состояния загрузчика —
   платформенная диагностика, а не контракт модуля.
6. **Сохранить `CapabilityRegistry`/`CapabilityScope` как advanced API.**
   Отклонено: два публичных способа для одной операции.
7. **Сохранить `ModuleRequirement.name`/`ModuleManifest.name` как
   identity.** Отклонено: два identity и скрытая связь display-имени с
   загрузкой.
8. **Сохранить старый `ItemApi` как алиас.** Отклонено: политика до
   `1.0` запрещает shim'ы.

## Последствия

- Capability-контракты становятся явными и проверяемыми.
- `requirements` по id остаются только для жизненного цикла; `base` не
  нужен OGG-редактору.
- Конфликт провайдеров обнаруживается на валидации, а не оставляет
  работающий модуль в неоднозначном состоянии.
- `ModuleManifest.id` — единственный identity; `name` больше не влияет
  на загрузку, `ModuleBase.manifest` удалён.
- `CapabilitiesApi` — единственный публичный контракт capability;
  registry/scope стали внутренней реализацией.
- Выгрузка провайдера требует согласованного каскада, но не оставляет
  висячих ссылок.
- Публичная поверхность SDK меняется контролируемо; версия `0.12.0`.

## Срок и условия выхода

Контракт действует до `1.0`. После `1.0` — semver, политика deprecation
и отдельное решение о внешних модулях/plugin-host.

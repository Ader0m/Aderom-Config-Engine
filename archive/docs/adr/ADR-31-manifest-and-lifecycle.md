# ADR-31. Манифест и жизненный цикл модуля

Дата: 24.11.2026
Статус: accepted

## Контекст

Сейчас манифест является атрибутом класса/объекта модуля
(`ModuleBase.manifest`), поэтому загрузчик импортирует код модуля до того,
как узнаёт его id, версию, зависимости, trust и совместимость с SDK.
Состояния загрузчика (`IMPORTED`, `INITIALIZED`, `REGISTERED`, `ACTIVE`,
`FAILED`, `SKIPPED`, `UNLOADED`) не включают `DISCOVERED`, `VALIDATED` и
`DISABLED`; пользовательское отключение не сохраняется; `config/modules.json`
ещё не существует; `reload`/`unload` не имеют единого контракта для окон,
задач и активных зависимых модулей. Внешних модулей пока нет, поэтому
переход можно сделать жёстким, без legacy-совместимости.

## Решение

### 1. Единственный манифест — `module.json`

Манифест читается до импорта кода модуля. `manifest` как атрибут
`ModuleBase` или класса модуля не читается и не поддерживается. Отсутствие
`module.json` — причина `SKIPPED`, а не повод искать старый class-manifest.
Миграции class-manifest нет: встроенные модули обновляются в том же
изменении, внешних модулей нет.

Пример:

```json
{
  "manifest_version": 1,
  "id": "example_module",
  "name": "Пример модуля",
  "version": "0.1.0",
  "description": "Короткое описание.",
  "author": "ACE",
  "entrypoint": "module:MODULE",
  "sdk": {
    "min": "0.12.0",
    "max": "1.0.0"
  },
  "requires": [
    {"id": "base", "min_version": "0.4.0"}
  ],
  "optional_requires": [],
  "needs": [
    {"name": "items.catalog", "min_version": "1.0.0"}
  ],
  "optional_needs": [
    {"name": "section.graph", "min_version": "1.0.0"}
  ],
  "provides": [
    {"name": "example.capability", "version": "1.0.0"}
  ],
  "trust": "trusted",
  "enabled_by_default": true,
  "platforms": ["windows"]
}
```

Правила схемы:

| Поле | Обяз. | Формат |
|---|---|---|
| `manifest_version` | да | целое; сейчас только `1` |
| `id` | да | `^[a-z][a-z0-9_]*$`; совпадает с именем каталога `modules/<id>` и namespace |
| `name` | да | отображаемое имя |
| `version` | да | semver модуля |
| `description`, `author` | нет | строки |
| `entrypoint` | да | `<подмодуль>:MODULE`; объект обязан быть `ModuleBase` |
| `sdk` | да | объект `{min, max}`; `min` обязателен, `max` может отсутствовать |
| `requires` | нет | массив `{id, min_version?}` |
| `optional_requires` | нет | массив `{id, min_version?}` |
| `needs` | нет | массив `{name, min_version}` |
| `optional_needs` | нет | массив `{name, min_version}` |
| `provides` | нет | массив `{name, version}` |
| `trust` | да | `core`, `trusted` или `untrusted` |
| `enabled_by_default` | да | boolean |
| `platforms` | нет | массив строк; по умолчанию `["windows"]` |

Строковые сокращения для зависимостей и capability не используются:
неоднозначность версии запрещена. Неизвестные поля верхнего уровня
игнорируются с предупреждением. Неизвестный `manifest_version` больше
текущего даёт `SKIPPED` с причиной `unsupported_manifest_version` без
импорта кода.

`entrypoint` импортирует указанный подмодуль и берёт атрибут `MODULE`
явно. `create_module()`, `getattr(..., "MODULE", None)` и поиск класса по
имени не поддерживаются.

Несовпадение `id`, каталога и `entrypoint` — `SKIPPED`. Ошибка импорта
или отсутствие `ModuleBase` — `FAILED` с фазой `import`.

#### 1.1. In-memory `ModuleManifest`

После перехода на файловый манифест identity модуля одна:

- `ModuleManifest.id` — единственный identity и namespace;
- `ModuleManifest.name` — только отображаемое имя, не используется для
  поиска, зависимостей, owner или capability;
- `ModuleBase.manifest` и class-атрибут `manifest` удаляются из
  публичного контракта; загрузчик не читает их и не создаёт
  `ModuleManifest` из класса;
- `ModuleRequirement` описывает зависимость **модуля**: его поле
  переименовывается из `name` в `id` в Этапе 7. `ModuleRequirement.name`
  не остаётся алиасом и не означает capability;
- capability-зависимости описываются отдельными DTO
  (`CapabilityRequirement`/`CapabilityDeclaration`), а не
  `ModuleRequirement`;
- старые имена `manifest`, `ModuleManifest.name` как id и
  `ModuleRequirement.name` не поддерживаются.

### 2. Состояния и переходы

Полное состояние загрузчика:

```text
DISCOVERED -> VALIDATED -> LOADED -> INITIALIZED -> REGISTERED -> ACTIVE
                                             \-> FAILED
     \-> SKIPPED
     \-> DISABLED
ACTIVE/REGISTERED/DISABLED -> UNLOADED (по явной операции)
```

| Состояние | Значение |
|---|---|
| `DISCOVERED` | найден `module.json` и пакет; код не импортирован |
| `VALIDATED` | проверены schema, id, SDK, trust, платформа, зависимости и capabilities |
| `LOADED` | импортирован `entrypoint`, создан объект модуля |
| `INITIALIZED` | успешно выполнен `init` |
| `REGISTERED` | успешно выполнен `register`; регистрации в `RegistrationScope` |
| `ACTIVE` | успешно выполнен `activate`; модуль работает |
| `FAILED` | ошибка фазы после допуска: import/init/register/activate/deactivate/unload |
| `SKIPPED` | модуль не допущен до кода: manifest/trust/platform/SDK/зависимости/capability |
| `DISABLED` | пользователь явно отключил модуль |
| `UNLOADED` | регистрации, окна, задачи и пакет сняты |

Правила:

- `SKIPPED` всегда содержит машиночитаемую причину
  (`ModuleSkip.kind`): `manifest_missing`, `manifest_invalid`,
  `unsupported_manifest_version`, `id_mismatch`, `platform_unsupported`,
  `sdk_incompatible`, `trust_untrusted`, `missing_required_module`,
  `missing_capability`, `capability_version`, `capability_conflict`,
  `provider_unloaded`, `dependency_skipped`, `cycle`.
- Конфликт `provides` обнаруживается на фазе `VALIDATED` по всем
  манифестам до `load`/`init`: оба провайдера получают `SKIPPED` с
  `capability_conflict`; обязательные потребители — `SKIPPED`, optional —
  `None`. Динамическое добавление второго провайдера к уже активному
  провайдеру запрещено: новый модуль получает `SKIPPED`, существующий
  провайдер и его потребители не деактивируются. Детали — ADR-32.
- `FAILED` всегда содержит фазу и текст ошибки; он не активируется и не
  оставляет регистраций/окон/задач. Следующий запуск начинает с
  `DISCOVERED` и повторяет попытку; автоматического вечного retry нет.
- `DISABLED` — единственное состояние пользовательского намерения,
  которое сохраняется между запусками.
- `FAILED`, `SKIPPED`, `LOADED`, `INITIALIZED`, `REGISTERED`, `ACTIVE`,
  `UNLOADED` не сохраняются в `config/modules.json`: они вычисляются
  заново и показываются в диагностике.
- При ошибке `init`/`register`/`activate` вызывается безопасный rollback:
  `deactivate`, если модуль успел получить состояние; `RegistrationScope.dispose`;
  закрытие окон/вкладок владельца; отмена задач владельца; удаление
  capability; удаление пакета из `sys.modules`.
- Зависимые модули не активируются, если обязательная зависимость
  перешла в `FAILED`/`SKIPPED`; они получают `SKIPPED` с причиной
  `dependency_skipped`.
- Ошибка одного модуля не прерывает загрузку остальных и не роняет
  платформу.

### 3. `config/modules.json`

Файл хранит только пользовательское намерение и версию схемы:

```json
{
  "schema_version": 1,
  "modules": {
    "base": {"enabled": true},
    "item_browser": {"enabled": false}
  }
}
```

- Запись с `enabled: false` означает `DISABLED`.
- Запись с `enabled: true` означает «загружать, если manifest и
  зависимости это разрешают».
- Отсутствующая запись означает `manifest.enabled_by_default`.
- Записи неизвестных id сохраняются, чтобы намерение не потерялось при
  временном отсутствии модуля.
- `schema_version` выше текущего: файл не интерпретируется, применяются
  defaults, пишется диагностическое сообщение. Файл становится
  read-only для приложения: enable/disable блокируется, пока схема не
  будет разрешена вручную; перезапись и даунгрейд запрещены.
- Атомарная запись, backup при повреждении и версия формата описаны в
  ADR-34.

### 4. `reload` и `unload`

`unload` — платформенная операция:

1. `deactivate` модуля (если он был активен).
2. `ctx.registrations.dispose()`.
3. Платформа закрывает оставшиеся окна/вкладки/доки **владельца**,
   если модуль не закрыл их сам; чужие ресурсы не трогаются.
4. Задачи владельца отменяются; ожидание идёт вне UI-потока.
5. Capability владельца удаляются.
6. Пакет удаляется из `sys.modules`; состояние `UNLOADED`.

`reload` = `unload` + повторный `DISCOVERED → VALIDATED → ... → ACTIVE`
для того же пакета. Старые регистрации, окна, вкладки и capability не
переживают reload.

Если у выгружаемого/отключаемого провайдера есть активные зависимые
модули:

- UI показывает список зависимых и требует явного подтверждения;
- обязательные зависимые деактивируются и получают `SKIPPED` с причиной
  `provider_unloaded`;
- необязательные зависимые деактивируются и пытаются активироваться
  снова в деградированном режиме; при ошибке получают `SKIPPED`/`FAILED`;
- после reload провайдера ранее активные зависимые восстанавливаются
  только если повторная валидация проходит;
- при закрытии приложения подтверждение не запрашивается, выполняется
  только безопасная остановка.

#### 4.1. Уточнение реализации шагов 7.2–7.3

Каскадная деактивация обязательных и optional-зависимых реализована на
шаге 7.2. На шаге 7.2 optional-зависимый, успешно поднятый в degraded
mode, оставался активным в этом режиме при возврате провайдера:
принудительная повторная инициализация была осознанно отложена, потому
что без публичного capability-контракта модуль не мог перечитать
возможность. На шаге 7.3 это закрыто: после возврата провайдера
`_restore_optional_dependents` переинициализирует активных degraded
optional-зависимых через `CapabilitiesApi`; их обязательные зависимые
снимаются и восстанавливаются вместе с ними. Optional-зависимые, у
которых degraded-активация не удалась (`FAILED`/`SKIPPED`), повторяются
при возврате провайдера через `_restore_provider_dependents`.

### 5. Trust

- `core` — код платформы/встроенный, полный доступ.
- `trusted` — проверенный модуль, работает в процессе приложения по
  контракту SDK.
- `untrusted` — сторонний код; в Этапе 7 не импортируется и получает
  `SKIPPED` с причиной `trust_untrusted`.
- Plugin-host для `untrusted` — отдельное решение после Этапа 7.
- Trust не является песочницей: `trusted`-модуль всё равно обязан
  соблюдать владение, запрет записи в оригиналы и контракт SDK.

## Альтернативы

1. **Оставить class-manifest и добавить `module.json` вторым путём.**
   Отклонено: два источника истины, код импортируется до проверки,
   появляется shim-совместимость.
2. **Сделать one-time migration class-manifest → `module.json`.**
   Отклонено: внешних модулей нет; миграция создала бы лишнюю ветку и
   маскировала бы несовместимость.
3. **Сохранять в `config/modules.json` все runtime-состояния.**
   Отклонено: `FAILED`/`SKIPPED` стали бы вторым источником истины и
   устаревали бы при изменении зависимостей.
4. **Разрешать `untrusted` в общем процессе с предупреждением.**
   Отклонено: trust без изоляции создаёт ложное обещание безопасности.
5. **Автоматически выбирать провайдера capability при конфликте.**
   Отклонено: скрытая замена поведения; выбор должен быть явным или
   конфликт остаётся ошибкой (см. ADR-32).
6. **Сохранить `ModuleManifest.name` как identity, добавив `id` вторым
   полем.** Отклонено: два identity и второй источник истины.
7. **При future-схеме `config/modules.json` перезаписать файл v1 после
   явного enable/disable.** Отклонено: потеря пользовательского
   состояния, созданного более новой версией; запись блокируется.

## Последствия

- Загрузчик получает фазы `DISCOVERED`/`VALIDATED` до импорта кода;
  ошибки манифеста и trust не выполняют код модуля.
- `config/modules.json` становится единственным местом пользовательского
  включения/отключения; статусы — вычисляемая диагностика.
- Встроенные модули переводятся на `module.json` и `MODULE` без
  class-manifest в том же изменении.
- `ModuleManifest.id` становится единственным identity; `name` —
  display-имя; `ModuleBase.manifest` и `ModuleRequirement.name` удаляются
  без алиасов (`ModuleRequirement.id` — id модуля).
- `reload`/`unload` получают единый owner-cleanup; каскад зависимых
  виден пользователю.
- Появляется явная причина каждого `SKIPPED`/`FAILED`.
- SDK-версия Этапа 7 повышается до `0.12.0`; class-manifest и старые
  имена не сохраняются.

## Срок и условия выхода

Контракт действует до `1.0`. После `1.0` изменения — по semver и
отдельной политике deprecation. Plugin-host и загрузка `untrusted` —
отдельный ADR после Этапа 7.

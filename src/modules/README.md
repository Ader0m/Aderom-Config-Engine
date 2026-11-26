# Модули ACE

Модуль — это **набор возможностей с владельцем**, а не набор глобальных
side-effect'ов. Контракт модулей описан в
`docs/Целевая архитектура модулей.md`.

## Общие правила

- Модуль импортирует только `sdk`, PyQt для собственных виджетов и свои
  third-party зависимости. Импорты `core`, `app`, `platform`, `ui` и
  других модулей запрещены.
- Единственный манифест — `module.json`. Class-manifest
  (`ModuleBase.manifest`, `create_module`) не поддерживается.
- Манифест читается и валидируется до импорта кода модуля.
- Зависимости по модулям — `requires`/`optional_requires`; зависимости
  по возможностям — `needs`/`optional_needs`; версия capability
  проверяется загрузчиком.
- Окна, вкладки, задачи и регистрации имеют владельца. Модуль не может
  закрыть чужое окно или изменить чужую регистрацию.
- Тяжёлая работа выполняется через `ctx.tasks`, а не в UI-потоке.
- Модуль не пишет в оригиналы и `Overwrite`; изменения идут через
  проект и патч-конвейер.
- `module.json` — источник истины для `id`, версии, `provides`/`needs`,
  `trust` и `enabled_by_default`.

## Встроенные модули

| Каталог | `id` | Назначение |
|---|---|---|
| `base` | `base` | LTX/XML viewers, граф секции; обязателен по ADR-35 |
| `item_browser` | `item_browser` | UI каталога, карточек и drag-and-drop предметов |
| `ogg_editor` | `ogg_editor` | Просмотр и редактирование комментариев `.ogg` |

`base` загружается всегда и не отключается через `config/modules.json`.
`item_browser` и `ogg_editor` можно отключать; `ogg_editor` требует
`base` по id и `section.graph` как обязательную capability.

## Проверка

- conformance: `tests/test_module_conformance.py`;
- манифесты и загрузчик: `tests/test_module_manifest.py`,
  `tests/test_module_loader.py`;
- границы импортов: `tests/test_architecture.py`;
- владение и выгрузка: `tests/test_editor_tabs.py`,
  `tests/test_module_windows.py`.

# base

Базовый модуль ACE. Обязателен по политике проекта (ADR-35): всегда
загружается обычным `ModuleLoader` по `module.json` и не отключается
через `config/modules.json`.

## Возможности

Предоставляет:

- `document.viewer.ltx` — открытие LTX (raw/resolved/DLTX);
- `document.viewer.xml` — открытие XML и XML-resolved;
- `section.graph` — граф связей секции.

`workspace`-бутстрап, выбор VFS и общие действия дерева/редактора
принадлежат платформе, а не модулю. Предметный UI и drag-and-drop
вынесены в `item_browser`.

## Манифест

- `id`: `base`
- `sdk.min`: `0.12.0`
- `requires`: нет
- `needs`: нет
- `enabled_by_default`: `true`
- `trust`: `trusted` (принимается только для id из `SHIPPED_MODULE_IDS`)

## Границы

Модуль импортирует только `sdk` и PyQt. Внешние возможности получает
через `ctx.services`, `ctx.ui`, `ctx.tasks`. Не импортирует `core`,
`app`, `ui` и другие модули. Не пишет оригиналы: правки идут через
проект и патч-конвейер.

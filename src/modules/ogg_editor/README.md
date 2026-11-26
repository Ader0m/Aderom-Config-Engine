# ogg_editor

Модуль просмотра и редактирования комментариев X-Ray в `.ogg`-файлах.
Работает с деревом VFS через `ctx.services`, с проектом и патчем — через
платформенные `ctx.projects`/`ctx.documents`.

## Возможности

Предоставляет:

- `audio.ogg.editor` — редактор комментариев `.ogg`.

Зависимости:

- `requires: [{"id": "base"}]` — намеренная зависимость по ADR-35;
- `needs: section.graph` — обязательная capability `base`;
- `optional_needs: item.section.dragdrop` — при отсутствии dragdrop
  редактор работает без интеграции с карточкой предмета.

## Манифест

- `id`: `ogg_editor`
- `sdk.min`: `0.13.0`
- `requires`: `base`
- `needs`: `section.graph`
- `optional_needs`: `item.section.dragdrop`
- `enabled_by_default`: `true`
- `trust`: `trusted` (принимается только для id из `SHIPPED_MODULE_IDS`)

## Границы

- Модуль импортирует только `sdk` и PyQt.
- Прямого импорта `core`, `app`, `ui` и других модулей нет.
- Оригиналы `.ogg` не изменяются; изменения идут через проектный
  черновик и патч-конвейер.
- Тяжёлая работа выполняется через `ctx.tasks`.

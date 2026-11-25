# -*- coding: utf-8 -*-
"""Платформенная политика доверия модулей (ADR-31).

``trust`` в ``module.json`` — заявление модуля, а не разрешение. Загрузчик
принимает ``core``/``trusted`` только для id из этого списка; всё
остальное считается ``untrusted`` и не импортируется. Список обновляется
вместе с составом поставляемых модулей.
"""

SHIPPED_MODULE_IDS = frozenset({
    "base",
    "item_browser",
    "ogg_editor",
})

# Обязательные модули проекта (ADR-35): всегда загружаются и не
# отключаются через config/modules.json.
REQUIRED_MODULE_IDS = frozenset({
    "base",
})

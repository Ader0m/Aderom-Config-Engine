# -*- coding: utf-8 -*-
"""Разбор текста UI X-Ray так, как это делает движок.

Движок читает ``<text>`` строковой таблицы как обычную строку
(``CStringTable``) и передаёт её в ``CUILines``. В комплексном режиме
``CUILines::ParseText``:

- вырезает цветовые метки ``%c[...]`` (``CutFirstColoredTextEntry``);
- ``%c[default]`` возвращает цвет текста контрола, имя ищется в
  ``configs/ui/color_defs.xml`` (``CUIXmlInit::InitColorDefs``), а
  ``%c[a,r,g,b]`` разбирается как ARGB (``GetColorFromText``);
- последовательность ``\\n`` превращается в перевод строки
  (``CUILine::ProcessNewLines`` / ветка мультибайтного шрифта);
- реальные ``CR``/``LF``/``TAB`` переводами строк не считаются: в
  шрифтовом атласе у управляющих символов нулевая ширина, поэтому
  движок их фактически не рисует.

Модуль намеренно не зависит ни от ``core``, ни от PyQt: карточка
получает готовые отрезки текста с цветом и сама рисует их в Qt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple


Color = Tuple[int, int, int, int]

_COLOR_TAG_RE = re.compile(r"%c\[([^\]]*)\]", re.IGNORECASE)
_COLOR_DEF_RE = re.compile(r"<color\b([^>]*)>", re.IGNORECASE)
_ATTR_RE = re.compile(
    r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""",
)


@dataclass(frozen=True)
class EngineTextRun:
    """Кусок текста с цветом движка (``None`` — цвет контрола)."""

    text: str
    color: Optional[Color] = None


def _byte(value) -> int:
    """Как ``atoi`` + маска ``& 0xff`` в ``color_argb``."""
    try:
        return int(str(value).strip() or 0) & 0xFF
    except (TypeError, ValueError):
        return 0


def _normalize_color(value) -> Optional[Color]:
    """Привести цвет к ``(r, g, b, a)``.

    Поддерживаются кортежи/списки и строки ``#RGB``, ``#RRGGBB`` и
    ``#AARRGGBB`` (последний вариант — как у Qt).
    """
    if value is None:
        return None

    if isinstance(value, (tuple, list)):
        if len(value) == 3:
            return (_byte(value[0]), _byte(value[1]), _byte(value[2]), 255)
        if len(value) >= 4:
            return (
                _byte(value[0]),
                _byte(value[1]),
                _byte(value[2]),
                _byte(value[3]),
            )
        return None

    text = str(value).strip()
    if not text:
        return None
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        if len(text) == 6:
            return (
                int(text[0:2], 16),
                int(text[2:4], 16),
                int(text[4:6], 16),
                255,
            )
        if len(text) == 8:
            return (
                int(text[2:4], 16),
                int(text[4:6], 16),
                int(text[6:8], 16),
                int(text[0:2], 16),
            )
    except ValueError:
        return None
    return None


def parse_color_definitions(text) -> Dict[str, Color]:
    """Разобрать ``configs/ui/color_defs.xml``.

    Движок читает только атрибуты ``name``/``r``/``g``/``b``/``a``
    (``CUIXmlInit::InitColorDefs``). Имена сравниваются без учёта
    регистра: в тексте метки обычно записаны как ``%c[ui_gray_3]``.
    """
    result: Dict[str, Color] = {}
    for match in _COLOR_DEF_RE.finditer(str(text or "")):
        attrs = {}
        for attr in _ATTR_RE.finditer(match.group(1)):
            key = attr.group(1).lower()
            value = attr.group(2)
            if value is None:
                value = attr.group(3)
            attrs[key] = value

        name = str(attrs.get("name", "")).strip()
        if not name:
            continue
        result[name.lower()] = (
            _byte(attrs.get("r", 0)),
            _byte(attrs.get("g", 0)),
            _byte(attrs.get("b", 0)),
            _byte(attrs.get("a", 255)),
        )
    return result


def _resolve_color(
    content: str,
    colors: Mapping[str, Color],
    default: Optional[Color],
) -> Optional[Color]:
    tag = str(content or "").strip()

    # %c[default] — цвет текста контрола, а не предыдущая метка.
    if tag.lower() == "default":
        return default

    named = colors.get(tag.lower())
    if named is not None:
        return named

    parts = [part.strip() for part in tag.split(",")]
    if len(parts) != 4:
        return default

    # В движке порядок именно ARGB: %c[a,r,g,b].
    a, r, g, b = (_byte(part) for part in parts)
    return (r, g, b, a)


def parse_engine_text(
    text,
    colors: Optional[Mapping[str, Color]] = None,
    default_color=None,
) -> Tuple[EngineTextRun, ...]:
    """Разбить строку на отрезки с цветом по правилам ``CUILines``.

    Метка без закрывающей ``]`` остаётся обычным текстом — так же, как
    в ``CutFirstColoredTextEntry``. Неизвестная метка (имя не найдено и
    это не четыре числа) даёт цвет контрола.
    """
    source = str(text or "")
    palette = {}
    for name, color in (colors or {}).items():
        normalized = _normalize_color(color)
        if normalized is not None:
            palette[str(name).lower()] = normalized
    default = _normalize_color(default_color)

    runs = []
    position = 0
    current = default

    for match in _COLOR_TAG_RE.finditer(source):
        if match.start() > position:
            runs.append(
                EngineTextRun(source[position:match.start()], current)
            )
        current = _resolve_color(match.group(1), palette, default)
        position = match.end()

    if position < len(source):
        runs.append(EngineTextRun(source[position:], current))

    return tuple(runs)


def engine_source_text(text) -> str:
    """Строка для редактора карточки.

    Реальные ``CR``/``LF``/``TAB`` — это форматирование XML-файла, а не
    содержимое строки: движок их не рисует. Поэтому в редактор они не
    попадают, иначе Qt покажет из них лишние переносы и огромные
    отступы. Литералы ``\\n`` и ``%c[...]`` сохраняются как есть.
    """
    return "".join(
        ch for ch in str(text or "") if ord(ch) >= 0x20
    )


def engine_text_lines(text) -> Tuple[str, ...]:
    """Текст без меток и с переводами строк (для тестов/превью).

    Реальные ``CR``/``LF``/``TAB`` движок рисует нулевой шириной: у
    управляющих символов в шрифтовом атласе нет глифа. Поэтому здесь
    они удаляются, а строки делит только литерал ``\\n``.
    """
    runs = parse_engine_text(text)
    plain = "".join(run.text for run in runs)
    plain = "".join(ch for ch in plain if ord(ch) >= 0x20)
    return tuple(plain.replace("\\n", "\n").split("\n"))

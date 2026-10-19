"""Лексика reference_graph: нормализация имён, токенизация, правила совпадения.

Правила совпадения токена с именем секции (пункт 3 спецификации, финал):
- токен == секция                        -> EXACT   (high);
- токен == <префикс>_<секция>            -> PREFIXED (medium):
  'x_raid_material_1', 'y_raid_material_1', '??_raid_material_1' — валидные совпадения;
- токен == <секция>_<суффикс>            -> не совпадение (флаг выключен по умолчанию);
- секция как подстрока без границы по '_' -> не совпадение:
  'raid_material_10' и 'raid_material_100' НЕ дают связи для 'raid_material_1'.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional, Tuple

# Разделители токенов. Точка включена: имена файлов ('raid_material_1.ltx')
# должны токенизироваться до 'raid_material_1'.
DELIMITERS = frozenset(' \t,;=()[]"<>\'`|/\\:.')
COMMENT_CHAR = ";"

# <секция>_<суффикс>: по умолчанию не считаем совпадением (решение v3, пункт 3).
INCLUDE_SUFFIX_FORM = False

RESOURCE_KEYS = frozenset({"icon", "inv_icon", "visual", "texture"})
RESOURCE_EXTENSIONS = (".dds", ".ogf", ".tga", ".png", ".bmp")


class MatchKind(Enum):
    EXACT = "exact"
    PREFIXED = "prefixed"


def normalize_name(raw: str) -> str:
    """Имена секций нормализуются как в движке: нижний регистр, без пробелов по краям."""
    return raw.strip().lower()


def match_token(token: str, section: str) -> Optional[MatchKind]:
    """Проверка токена по правилам пункта 3. None = не совпадение.

    >>> match_token("raid_material_1", "raid_material_1") is MatchKind.EXACT
    True
    >>> match_token("x_raid_material_1", "raid_material_1") is MatchKind.PREFIXED
    True
    >>> match_token("raid_material_10", "raid_material_1") is None
    True
    """
    if token == section:
        return MatchKind.EXACT
    if len(token) > len(section) + 1 and token.endswith("_" + section):
        return MatchKind.PREFIXED
    if INCLUDE_SUFFIX_FORM and len(token) > len(section) + 1 and token.startswith(section + "_"):
        return MatchKind.PREFIXED
    return None


def tokenize(text: str) -> Tuple[str, ...]:
    """Разбиение строки на токены по DELIMITERS. Пустые токены отбрасываются."""
    tokens: List[str] = []
    buffer: List[str] = []
    for char in text:
        if char in DELIMITERS:
            if buffer:
                tokens.append("".join(buffer))
                buffer.clear()
        else:
            buffer.append(char)
    if buffer:
        tokens.append("".join(buffer))
    return tuple(tokens)


def split_comment(line: str) -> Tuple[str, Optional[str]]:
    """Разделяет строку на (код, комментарий). ';' внутри кавычек не считается комментарием.

    Комментарии отсутствуют в итоговом дереве (движок их не видит), поэтому
    low-связи из комментариев строятся по сырым файлам (решение 8.3).
    """
    quote: Optional[str] = None
    for index, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == COMMENT_CHAR:
            return line[:index], line[index + 1:]
    return line, None


def looks_like_resource_path(key: str, value: str) -> bool:
    """Признак значения-пути ресурса для ребра image (icon/visual/texture и т.п.)."""
    value_stripped = value.strip()
    if not value_stripped:
        return False
    if normalize_name(key) in RESOURCE_KEYS:
        return True
    lowered = value_stripped.lower()
    return ("\\" in lowered or "/" in lowered) and lowered.endswith(RESOURCE_EXTENSIONS)
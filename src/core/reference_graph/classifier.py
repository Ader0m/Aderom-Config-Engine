"""Классификатор связей reference_graph: происхождение + вид совпадения токена -> метка и сила.

Таблица меток (спецификация v3):
- KEY/VALUE в итоговом дереве: EXACT -> high, PREFIXED -> medium;
  для ltx-упоминаний метка ребра совпадает с силой;
- COMMENT ('; текст') и FILENAME -> low: движок их не видит, видит человек;
- XML / SCRIPT -> метки xml/script, сила по тем же токен-правилам;
- IMAGE, PARENT, CHILD -> создаются билдером напрямую, минуя классификатор.
"""
from __future__ import annotations

from typing import Tuple

from .models import EdgeLabel, Mention, MentionOrigin, Strength
from .tokenizer import MatchKind

_STRENGTH_LABEL = {
    Strength.HIGH: EdgeLabel.HIGH,
    Strength.MEDIUM: EdgeLabel.MEDIUM,
    Strength.LOW: EdgeLabel.LOW,
}


def _strength(kind: MatchKind) -> Strength:
    return Strength.HIGH if kind is MatchKind.EXACT else Strength.MEDIUM


def classify(origin: MentionOrigin, kind: MatchKind) -> Tuple[EdgeLabel, Strength]:
    """Чистая функция: (происхождение, вид совпадения) -> (метка ребра, сила)."""
    if origin is MentionOrigin.DEFINITION:
        return EdgeLabel.HIGH, Strength.HIGH
    if origin in (MentionOrigin.COMMENT, MentionOrigin.FILENAME):
        return EdgeLabel.LOW, Strength.LOW
    if origin is MentionOrigin.XML:
        return EdgeLabel.XML, _strength(kind)
    if origin is MentionOrigin.SCRIPT:
        return EdgeLabel.SCRIPT, _strength(kind)
    # KEY / VALUE итогового дерева
    strength = _strength(kind)
    return _STRENGTH_LABEL[strength], strength


def aggregate_edge(mentions: Tuple[Mention, ...]) -> Tuple[EdgeLabel, Strength]:
    """Агрегация в одно ребро на пару (центр, узел).

    Сила = max по упоминаниям; метка = метка сильнейшего упоминания
    (первого встретившегося при стабильном порядке упоминаний —
    порядок построения индекса детерминирован).
    """
    if not mentions:
        raise ValueError("aggregate_edge: пустой набор упоминаний")
    best = mentions[0]
    for mention in mentions[1:]:
        if mention.strength > best.strength:
            best = mention
    return best.label, best.strength
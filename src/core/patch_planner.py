# -*- coding: utf-8 -*-
"""PatchPlanner — уровень 'Сохранить как'."""


class PatchPlanner:
    """
    Планировщик патчей.

    В старом core.py эта функциональность отсутствовала.
    Класс создан для соответствия утверждённой архитектуре.

    Ответственность будущего реализации:
    - анализ исходного состояния;
    - сравнение с желаемым состоянием;
    - выделение только дельты;
    - определение целевого патч-мода;
    - подготовка preview;
    - передача плана в DltxGenerator или DxmlGenerator.
    """

    def __init__(self, resolver_adapter=None, cache=None):
        self.resolver_adapter = resolver_adapter
        self.cache = cache

    def plan_text_patch(self, original_path, desired_text, target_mod=None):
        raise NotImplementedError(
            "PatchPlanner.plan_text_patch ещё не реализован. "
            "В старом core.py этой логики не было."
        )

    def plan_property_patch(
        self,
        original_path,
        section_name,
        key_name,
        desired_value,
        target_mod=None,
    ):
        raise NotImplementedError(
            "PatchPlanner.plan_property_patch ещё не реализован. "
            "В старом core.py этой логики не было."
        )

    def preview(self, plan):
        raise NotImplementedError(
            "PatchPlanner.preview ещё не реализован."
        )
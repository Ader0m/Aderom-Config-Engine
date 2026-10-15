# -*- coding: utf-8 -*-
"""DltxGenerator — уровень 'Сохранить' для LTX."""


class DltxGenerator:
    """
    Генератор DLTX-дельты.

    В старом core.py эта функциональность отсутствовала.
    Класс создан для соответствия утверждённой архитектуре.

    Будущая ответственность:
    - брать план патча от PatchPlanner;
    - формировать минимальную DLTX-дельту;
    - готовить файлы патч-мода;
    - не изменять оригинальные файлы.
    """

    def __init__(self, resolver_adapter=None, cache=None):
        self.resolver_adapter = resolver_adapter
        self.cache = cache

    def generate(self, plan):
        raise NotImplementedError(
            "DltxGenerator.generate ещё не реализован. "
            "В старом core.py этой логики не было."
        )
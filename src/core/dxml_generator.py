# -*- coding: utf-8 -*-
"""DxmlGenerator — уровень 'Сохранить' для XML."""


class DxmlGenerator:
    """
    Генератор DXML-дельты.

    В старом core.py эта функциональность отсутствовала.
    Класс создан для соответствия утверждённой архитектуре.

    Будущая ответственность:
    - брать план патча от PatchPlanner;
    - формировать минимальную DXML-дельту;
    - учитывать структуру XML;
    - готовить файлы патч-мода;
    - не изменять оригинальные файлы.
    """

    def __init__(self, resolver_adapter=None, cache=None):
        self.resolver_adapter = resolver_adapter
        self.cache = cache

    def generate(self, plan):
        raise NotImplementedError(
            "DxmlGenerator.generate ещё не реализован. "
            "В старом core.py этой логики не было."
        )
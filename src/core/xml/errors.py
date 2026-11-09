# -*- coding: utf-8 -*-
"""Базовые ошибки XML-слоя."""

from __future__ import annotations


class XmlModelError(Exception):
    """XML-текст нельзя представить моделью или селектором."""

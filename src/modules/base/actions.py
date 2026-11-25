# -*- coding: utf-8 -*-
"""Предикаты LTX/XML-действий базового модуля."""

from pathlib import Path


def is_xml_path(path) -> bool:
    """True для файлов с расширением .xml."""
    return Path(path).suffix.lower() == ".xml"


def is_ltx_path(path) -> bool:
    """True для путей, которые открываются LTX-путём (не XML)."""
    return Path(path).suffix.lower() != ".xml"

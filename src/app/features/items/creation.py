# -*- coding: utf-8 -*-
"""Доменные правила создания предмета.

Модуль ``item_browser`` только собирает входные данные и показывает
ошибки; тексты новой секции и строк локализации формируются здесь.
"""

from __future__ import annotations

import re

from pathlib import Path

from sdk.features.items.models import ItemCreationPlan


def project_tag(name) -> str:
    """Безопасный тег проекта для имён файлов и ключей локализации."""
    value = str(name or "").strip().lower()
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE)
    return value.strip("._-") or "project"


def creation_paths(scan_dir, project_name):
    """Пути файлов проекта для нового предмета."""
    tag = project_tag(project_name)
    root = Path(scan_dir)
    item_path = root / "items" / ("mod_item_base_~ace_%s.ltx" % tag)
    xml_path = root / "text" / "rus" / ("st_ace_%s.xml" % tag)
    return item_path, xml_path


def parent_fields(catalog, parent):
    """Поля родительской секции/предмета для новой секции."""
    if not parent:
        return ()
    wanted = str(parent).strip().lower()
    fields = []
    for section in getattr(catalog, "sections", ()) or ():
        if section.name.lower() == wanted:
            fields.extend(section.fields or ())
            break
    for entry in getattr(catalog, "items", ()) or ():
        if entry.section.lower() == wanted:
            fields.extend(field.key for field in entry.fields)
            break
    result = []
    seen = set()
    for key in fields:
        value = str(key or "").strip()
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        result.append(value)
    return tuple(result)


def _next_localization_base(base_key: str, existing_ids) -> str:
    existing = {str(value).lower() for value in existing_ids or ()}

    if (
        (base_key + "_name").lower() not in existing
        and (base_key + "_descr").lower() not in existing
    ):
        return base_key

    index = 2
    while True:
        candidate = "%s_%d" % (base_key, index)
        if (
            (candidate + "_name").lower() not in existing
            and (candidate + "_descr").lower() not in existing
        ):
            return candidate
        index += 1


def _build_item_section(
    section: str,
    parent,
    parent_fields,
    name_key: str,
    description_key: str,
    newline: str = "\n",
) -> str:
    header = "[%s]" % section
    parent = str(parent or "").strip()
    if parent:
        header += ":%s" % parent

    lines = [header]
    seen = set()
    for key in parent_fields or ():
        key = str(key or "").strip()
        if not key:
            continue
        lower = key.lower()
        if lower in ("inv_name", "inv_name_short", "description"):
            continue
        if lower in seen:
            continue
        seen.add(lower)
        lines.append("%s =" % key)

    lines.append("inv_name = %s" % name_key)
    lines.append("inv_name_short = %s" % name_key)
    lines.append("description = %s" % description_key)
    return newline.join(lines)


def _append_item_section(text: str, block: str, newline: str = "\n") -> str:
    text = str(text or "")
    if not text.strip():
        return block + newline
    return text.rstrip() + newline + newline + block + newline


def _xml_string_entries(
    name_key: str,
    description_key: str,
    newline: str = "\n",
) -> str:
    lines = []
    for key in (name_key, description_key):
        lines.extend((
            '\t<string id="%s">' % key,
            "\t\t<text></text>",
            "\t</string>",
        ))
    return newline.join(lines)


def _append_xml_strings(
    text: str,
    name_key: str,
    description_key: str,
    newline: str = "\n",
) -> str:
    block = _xml_string_entries(name_key, description_key, newline)
    text = str(text or "")

    if not text.strip():
        return newline.join((
            '<?xml version="1.0" encoding="windows-1251"?>',
            "",
            "<string_table>",
            block,
            "</string_table>",
            "",
        ))

    marker = "</string_table>"
    index = text.lower().rfind(marker)
    if index == -1:
        return text.rstrip() + newline + newline + block + newline

    return text[:index].rstrip() + newline + block + newline + text[index:]


def plan_item_creation(
    item_text: str,
    xml_text: str,
    known_sections,
    section: str,
    parent,
    parent_fields,
    project_name,
) -> ItemCreationPlan:
    """Построить тексты нового предмета, не записывая файлы."""
    section = str(section or "").strip()
    if not section:
        raise ValueError("Не задано имя секции предмета.")
    if re.search(r"[\[\]:\r\n]", section):
        raise ValueError(
            "Имя секции не должно содержать [ ], : или перевод строки."
        )

    known = {str(value).lower() for value in known_sections or ()}
    if section.lower() in known:
        raise ValueError("Секция [%s] уже существует." % section)

    tag = project_tag(project_name)
    item_newline = "\r\n" if "\r\n" in item_text else "\n"
    xml_newline = "\r\n" if "\r\n" in xml_text else "\n"

    base_key = "st_ace_%s" % tag
    existing_ids = re.findall(
        r'<string\s+id\s*=\s*"([^"]+)"',
        xml_text,
        flags=re.IGNORECASE,
    )
    key_base = _next_localization_base(base_key, existing_ids)
    name_key = key_base + "_name"
    description_key = key_base + "_descr"

    block = _build_item_section(
        section,
        parent,
        parent_fields,
        name_key,
        description_key,
        newline=item_newline,
    )
    item_text = _append_item_section(
        item_text,
        block,
        newline=item_newline,
    )
    xml_text = _append_xml_strings(
        xml_text,
        name_key,
        description_key,
        newline=xml_newline,
    )
    return ItemCreationPlan(
        section=section,
        tag=tag,
        item_text=item_text,
        xml_text=xml_text,
        name_key=name_key,
        description_key=description_key,
    )

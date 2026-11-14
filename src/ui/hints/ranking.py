# -*- coding: utf-8 -*-
"""Общие правила отбора элементов подсказки."""

from __future__ import annotations

MAX_ITEMS = 200


def dedupe_items(items, limit=MAX_ITEMS):
    seen = set()
    result = []
    for item in items:
        key = (str(getattr(item, "insert_text", "")), str(getattr(item, "kind", "")))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= int(limit):
            break
    return tuple(result)


def provider_applies(provider, context):
    surfaces = getattr(provider, "surfaces", frozenset()) or frozenset()
    languages = getattr(provider, "languages", frozenset()) or frozenset()
    if surfaces and "*" not in surfaces and context.surface not in surfaces:
        return False
    if languages and "*" not in languages and context.language not in languages:
        return False
    if context.in_comment and not bool(
        getattr(provider, "allow_in_comment", False)
    ):
        return False
    return True


def validate_result(result, context):
    before = int(getattr(result, "replace_before", 0) or 0)
    after = int(getattr(result, "replace_after", 0) or 0)
    if before < 0 or after < 0:
        return False
    if before > len(context.prefix) or after > len(context.suffix):
        return False
    text = str(getattr(result, "suffix", "") or "")
    if "\n" in text or "\r" in text:
        return False
    for item in getattr(result, "items", ()) or ():
        if "\n" in str(item.insert_text) or "\r" in str(item.insert_text):
            return False
    return True


def result_total(result, items):
    total = int(getattr(result, "total", 0) or 0)
    return total if total > 0 else len(items)

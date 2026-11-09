# -*- coding: utf-8 -*-
"""DltxGenerator — генерация DLTX-дельты по плану патча.

Генератор не решает, куда писать. Он создаёт текст патч-файла и модель
файлов патч-мода. Запись выполняет отдельный безопасный писатель.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.patching.models import GeneratedPatch, PatchFile, PatchPlan
from core.patching.targets import patch_mod_file_rel


class PatchGenerationError(Exception):
    """Патч нельзя корректно сгенерировать."""


def _format_value(value: Optional[str]) -> str:
    if value is None:
        return ""

    text = str(value)

    if "\n" in text or "\r" in text:
        stripped = text.strip()
        if not (stripped.startswith('"') and stripped.endswith('"')):
            raise PatchGenerationError(
                "Многострочное значение должно быть заключено в кавычки"
            )
        return stripped

    return text


def _header(kind: str, name: str, parents: Tuple[str, ...] = ()) -> str:
    if kind == "delete":
        return "!![%s]" % name

    prefix = {
        "base": "",
        "override": "!",
        "safe": "@",
    }[kind]

    line = "%s[%s]" % (prefix, name)
    if parents:
        line += ":" + ",".join(parents)
    return line


def _render_entries(
    values: List[Tuple[str, Optional[str]]],
    list_ops: List[Tuple[str, str, Optional[str]]],
    deletes: List[str],
) -> List[str]:
    lines: List[str] = []

    for key, value in sorted(values, key=lambda item: item[0]):
        lines.append("%s = %s" % (key, _format_value(value)))

    # Порядок операций для одного ключа важен (append/remove), поэтому
    # сортируем только по ключу, сохраняя исходный порядок внутри ключа.
    for operation, key, value in sorted(
        list_ops,
        key=lambda item: item[1],
    ):
        marker = ">" if operation == "append" else "<"
        lines.append("%s%s = %s" % (marker, key, _format_value(value)))

    for key in sorted(deletes):
        lines.append("!%s" % key)

    return lines


def render_dltx(plan: PatchPlan) -> str:
    """Отрендерить DLTX-текст по плану."""
    if plan.is_empty:
        return ""

    added: Dict[str, object] = {}
    existing: Dict[str, dict] = {}
    deleted_sections: List[str] = []

    for change in plan.changes:
        if change.kind == "add_section":
            added[change.section] = change.section_snapshot
            continue

        if change.kind == "delete_section":
            deleted_sections.append(change.section)
            continue

        section = existing.setdefault(
            change.section,
            {
                "parents": [],
                "values": [],
                "list_ops": [],
                "deletes": [],
            },
        )

        if change.kind == "add_parent":
            section["parents"].append(change.parent)
        elif change.kind == "remove_parent":
            section["parents"].append("!" + str(change.parent))
        elif change.kind in ("add_key", "set_key"):
            section["values"].append((change.key, change.new_value))
        elif change.kind == "delete_key":
            section["deletes"].append(change.key)
        elif change.kind == "list_append":
            section["list_ops"].append(("append", change.key, change.new_value))
        elif change.kind == "list_remove":
            section["list_ops"].append(("remove", change.key, change.new_value))

    lines: List[str] = [
        "; ACE generated DLTX patch",
        "; Source: %s" % plan.source_path,
        "; Root: %s" % (
            plan.target.root_path if plan.target.root_path else "?"
        ),
        "; Changes: %d" % len(plan.changes),
    ]

    if plan.warnings or plan.issues:
        lines.append(";")
        lines.append("; Checks:")
        for issue in tuple(plan.issues) + tuple(plan.warnings):
            lines.append("; [%s] %s" % (issue.severity, issue.message))

    lines.append("")

    for name in sorted(added):
        snapshot = added[name]
        lines.append(_header("safe", name, snapshot.parents))
        lines.extend(
            _render_entries(
                list(snapshot.values),
                list(snapshot.list_ops),
                [],
            )
        )
        lines.append("")

    for name in sorted(existing):
        data = existing[name]
        parents = tuple(data["parents"])
        lines.append(_header("override", name, parents))
        lines.extend(
            _render_entries(
                data["values"],
                data["list_ops"],
                data["deletes"],
            )
        )
        lines.append("")

    for name in sorted(set(deleted_sections)):
        lines.append(_header("delete", name))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


class DltxGenerator:
    """Генератор DLTX-дельты."""

    def __init__(self, resolver_adapter=None, cache=None):
        self.resolver_adapter = resolver_adapter
        self.cache = cache

    def generate(self, plan: PatchPlan) -> GeneratedPatch:
        if plan.target.root_path is None:
            raise PatchGenerationError(
                "Не определён корень загрузки для патча"
            )

        if plan.has_errors:
            raise PatchGenerationError(
                "План содержит ошибки: %s"
                % "; ".join(issue.message for issue in plan.errors())
            )

        text = render_dltx(plan)

        if not text.strip():
            return GeneratedPatch(
                plan=plan,
                files=(),
                text="",
                verification=None,
            )

        if plan.patch_file_rel is not None:
            relative = Path("gamedata") / "configs" / plan.patch_file_rel
        else:
            relative = patch_mod_file_rel(
                None,
                plan.target.root_path,
                "ace",
            )

        file = PatchFile(
            relative_path=relative,
            text=text,
            encoding=plan.encoding,
        )

        return GeneratedPatch(
            plan=plan,
            files=(file,),
            text=text,
            verification=None,
        )

    def preview(self, plan: PatchPlan) -> str:
        return render_dltx(plan)

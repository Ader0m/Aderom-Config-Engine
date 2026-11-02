# -*- coding: utf-8 -*-
"""Граф связей секции: построение через ядро и текстовая презентация."""

from pathlib import Path


class SectionGraphFeature:
    """Запрашивает секцию, строит граф и показывает отчёт в редакторе."""

    def __init__(self, ctx, session):
        self.ctx = ctx
        self.session = session

    def show(self):
        if not self.session.has_tree:
            self.ctx.ui.show_error(
                "Дерево не построено: сначала «Чистый запуск»."
            )
            return

        section_name = self.ctx.ui.ask_text(
            "Имя секции:",
            "raid_material_1",
        )
        if not section_name:
            return

        self.ctx.ui.set_status("Построение графа: %s…" % section_name)

        forest = self.session.forest

        def work(log):
            return self.ctx.services.build_section_graph(forest, section_name)

        self.ctx.tasks.run(
            work,
            on_done=lambda graph, err: self._on_done(
                graph,
                err,
                section_name,
            ),
            on_log=self.ctx.ui.log,
        )

    def _on_done(self, graph, err, section_name):
        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.show_error(
                "Не удалось построить граф для '%s':\n%s"
                % (section_name, err)
            )
            return

        self.ctx.ui.set_status(
            "Граф построен: %d узлов" % len(graph.children),
            5000,
        )
        self.ctx.ui.show_text(
            format_section_graph(graph),
            "Граф связей: %s" % graph.section_name,
            readonly=True,
        )


def format_section_graph(graph) -> str:
    """Преобразовать SDK-модель графа в читаемый текстовый отчёт."""
    lines = [
        "Секция: %s" % graph.section_name,
        "Объявлена в: %s" % _path_text(graph.root.file_path),
        "Строка: %s" % graph.root.first_match_line,
        "Найдено файлов: %d" % len(graph.children),
        "",
        "[ОБЪЯВЛЕНИЕ] %s (строка %s)"
        % (_path_name(graph.root.file_path), graph.root.first_match_line),
        "",
    ]

    for node in graph.children:
        if node.is_texture:
            lines.append("[TEXTURE] %s" % node.raw_ref)
            lines.append(
                "    icons_texture объявлена в %s, строка %s"
                % (_path_name(node.file_path), node.first_match_line)
            )
            continue

        lines.append(
            "%s  [совпадений: %d, типы: %s, первая строка: %s]"
            % (
                node.display_name,
                node.matches_count,
                ", ".join(node.edge_types),
                node.first_match_line,
            )
        )
        lines.append("    %s" % _path_text(node.file_path))

    return "\n".join(lines)


def _path_text(path) -> str:
    return str(path) if path is not None else "—"


def _path_name(path) -> str:
    return Path(path).name if path is not None else "—"

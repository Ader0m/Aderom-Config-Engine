# -*- coding: utf-8 -*-
"""Открытие LTX-файлов: резолв базовых файлов и просмотр DLTX-патчей."""

from pathlib import Path

from sdk import FileOpenHandler


class LtxFileOpener:
    """Показывает .ltx так, как его видит игра, либо как исходный патч."""

    def __init__(self, ctx, session):
        self.ctx = ctx
        self.session = session

    def open(self, path) -> bool:
        if not self.session.has_tree:
            self.ctx.ui.show_error(
                "Дерево не построено: сначала «Чистый запуск»."
            )
            return False

        path = Path(path)
        if path.name.lower().startswith("mod_"):
            return self._open_patch(path)

        return self._open_resolved(path)

    # -------------------------------------------------- DLTX-патч
    def _open_patch(self, path: Path) -> bool:
        self.ctx.ui.set_status("Патч: %s…" % path.name)

        try:
            raw_content = self.ctx.services.read_text(path)
        except Exception as exc:
            self.ctx.ui.show_error("Не удалось прочитать: %s" % exc)
            return False

        forest = self.session.forest
        root = self.ctx.services.patch_target(forest, path)

        header = [
            "; ═══════════════════════════════════════════════════════════",
            ";                    DLTX-ПАТЧ",
            "; ═══════════════════════════════════════════════════════════",
            "; Файл: %s" % path.name,
            "; Тип: Патч (не загружается отдельно движком)",
        ]

        if root is not None:
            header += [
                "; Применяется к: %s"
                % self.ctx.services.relative_path(forest, root),
                "; ",
                "; Чтобы увидеть итоговое состояние с применённым патчем,",
                ";   откройте корневой файл: %s" % Path(root).name,
            ]
        else:
            header += [
                "; ",
                "; ⚠️ Патч не подключился ни к одному базовому файлу!",
                "; Проверьте имя (должно быть mod_<имя_базы>_<что-то>.ltx)",
            ]

        header += [
            "; ═══════════════════════════════════════════════════════════",
            "",
        ]

        rel = self.ctx.services.relative_path(forest, path)
        title = "DLTX PATCH: %s" % rel
        if root is None:
            title = "⚠️ " + title

        self.ctx.ui.show_text(
            "\n".join(header) + raw_content,
            title,
            readonly=True,
        )
        self.ctx.ui.set_status("Открыт патч: %s" % rel, 5000)
        return True

    # -------------------------------------------------- резолв
    def _open_resolved(self, path: Path) -> bool:
        self.ctx.ui.set_status("Резолв: %s…" % path.name)

        forest = self.session.forest
        config_dir = self.session.config_dir

        def work(log):
            return self.ctx.services.assemble_text(
                path,
                forest,
                config_dir,
                log=log,
            )

        self.ctx.tasks.run(
            work,
            on_done=lambda text, err: self._on_resolved(path, text, err),
            on_log=self.ctx.ui.log,
        )
        return True

    def _on_resolved(self, path: Path, text, err):
        if err is not None:
            self.ctx.ui.set_status("Ошибка")
            self.ctx.ui.show_error(
                "Не удалось разрешить %s:\n%s\n\n"
                "Исправьте файл через ПКМ -> Открыть исходный файл"
                % (path.name, err)
            )
            return

        rel = self.ctx.services.relative_path(self.session.forest, path)
        self.ctx.ui.show_text(
            text,
            "RESOLVED (как в игре): %s" % rel,
            readonly=True,
        )
        self.ctx.ui.set_status("Открыт резолв: %s" % rel, 5000)


class ResolvedLtxHandler(FileOpenHandler):
    """Перехватывает открытие .ltx и делегирует его :class:`LtxFileOpener`."""

    priority = 200

    def __init__(self, opener: LtxFileOpener):
        self._opener = opener

    def can_open(self, path):
        return Path(path).suffix.lower() == ".ltx"

    def open(self, path, api):
        return self._opener.open(path)

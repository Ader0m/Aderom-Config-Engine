# -*- coding: utf-8 -*-
"""Тесты маршрутизации открытия файлов."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from sdk.ui import FileOpenHandler
from ui.file_open_router import FileOpenRouter
from ui.platform.file_open_preferences import PREFERENCES_VERSION


class _Api:
    def __init__(self):
        self.logs = []

    def log(self, message):
        self.logs.append(message)


class _Handler(FileOpenHandler):
    def __init__(self, name, priority=0, can_open=True, result=True,
                 contexts=None, handler_id=None):
        self.name = name
        self.priority = priority
        self._can_open = can_open
        self._result = result
        self.calls = []
        if contexts is not None:
            self.contexts = tuple(contexts)
        # В тестах имя уже уникально; явный handler_id="" проверяет
        # дубликаты без идентификатора.
        self.handler_id = name if handler_id is None else handler_id

    def can_open(self, path):
        return self._can_open

    def open(self, path, api):
        self.calls.append(path)
        return self._result


class FileOpenRouterTest(unittest.TestCase):
    def setUp(self):
        self.api = _Api()

    def test_highest_priority_handler_wins(self):
        router = FileOpenRouter()
        low = _Handler("low", priority=1)
        high = _Handler("high", priority=10)
        router.register(low)
        router.register(high)

        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(low.calls, [])
        self.assertEqual(high.calls, [Path("a.ltx")])

    def test_handler_that_cannot_open_is_skipped(self):
        router = FileOpenRouter()
        refusing = _Handler("refusing", priority=100, can_open=False)
        accepting = _Handler("accepting", priority=0)
        router.register(refusing)
        router.register(accepting)

        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(refusing.calls, [])
        self.assertEqual(accepting.calls, [Path("a.ltx")])

    def test_selected_handler_result_does_not_fall_through(self):
        router = FileOpenRouter()
        first = _Handler("first", priority=10, result=False)
        second = _Handler("second", priority=0, result=True)
        router.register(first)
        router.register(second)

        # Маршрутизатор считает файл обработанным, если выбранный
        # обработчик согласился его открыть; false не запускает цепочку
        # заново. Фиксируем текущий контракт, чтобы не потерять его.
        self.assertFalse(router.open(Path("a.ltx"), self.api))
        self.assertEqual(first.calls, [Path("a.ltx")])
        self.assertEqual(second.calls, [])

    def test_module_handler_receives_module_context(self):
        router = FileOpenRouter()
        handler = _Handler("module")
        context = object()
        router.register(handler, context=context)

        seen = []
        handler.open = lambda path, api: seen.append(api) or True
        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(seen, [context])

    def test_conflict_at_same_priority_is_reported(self):
        router = FileOpenRouter()
        first = _Handler("first", priority=10)
        second = _Handler("second", priority=10)
        router.register(first, owner="mod_a")
        router.register(second, owner="mod_b")

        with self.assertLogs("ui.file_open_router", level="WARNING") as logs:
            self.assertTrue(router.open(Path("a.ltx"), self.api))

        self.assertIn("Конфликт обработчиков открытия", "\n".join(logs.output))
        self.assertEqual(first.calls, [Path("a.ltx")])
        self.assertEqual(second.calls, [])

    def test_can_open_error_skips_to_next_handler(self):
        router = FileOpenRouter()
        broken = _Handler("broken", priority=100)
        working = _Handler("working", priority=0)

        def fail_can_open(_path):
            raise RuntimeError("broken matcher")

        broken.can_open = fail_can_open
        router.register(broken)
        router.register(working)

        with self.assertLogs("ui.file_open_router", level="ERROR"):
            self.assertTrue(router.open(Path("a.ltx"), self.api))

        self.assertEqual(broken.calls, [])
        self.assertEqual(working.calls, [Path("a.ltx")])

    def test_default_handler_is_used_when_nobody_can_open(self):
        router = FileOpenRouter()
        handler = _Handler("handler", can_open=False)
        default = _Handler("default")
        router.register(handler)
        router.set_default(default)

        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(handler.calls, [])
        self.assertEqual(default.calls, [Path("a.ltx")])

    def test_no_handler_and_no_default_returns_false(self):
        router = FileOpenRouter()
        self.assertFalse(router.open(Path("a.ltx"), self.api))

    def test_open_default_bypasses_registered_handlers(self):
        router = FileOpenRouter()
        handler = _Handler("handler")
        default = _Handler("default")
        router.register(handler)
        router.set_default(default)

        self.assertTrue(router.open_default(Path("a.ltx"), self.api))
        self.assertEqual(handler.calls, [])
        self.assertEqual(default.calls, [Path("a.ltx")])

    def test_context_limits_registered_handlers(self):
        router = FileOpenRouter()
        view = _Handler("view", priority=10, contexts=("view",))
        preview = _Handler("preview", priority=10, contexts=("preview",))
        router.register(view)
        router.register(preview)

        self.assertTrue(router.open(Path("a.ltx"), self.api, context="view"))
        self.assertEqual(view.calls, [Path("a.ltx")])
        self.assertEqual(preview.calls, [])

        self.assertTrue(
            router.open(Path("a.ltx"), self.api, context="preview")
        )
        self.assertEqual(view.calls, [Path("a.ltx")])
        self.assertEqual(preview.calls, [Path("a.ltx")])

    def test_corrupted_preferences_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "file_open.json"
            path.write_text("{not json", encoding="utf-8")
            router = FileOpenRouter(preferences_path=path)
            self.assertEqual(router.preference(".ltx", "view"), "")

    def test_non_conforming_handler_is_ignored(self):
        class PlainHandler:
            priority = 100

            def can_open(self, path):
                return True

            def open(self, path, api):
                raise AssertionError("plain handler must not be called")

        router = FileOpenRouter()
        default = _Handler("default")
        router.set_default(default)
        with self.assertLogs("ui.file_open_router", level="WARNING"):
            router.register(PlainHandler())

        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(default.calls, [Path("a.ltx")])

    def test_invalid_contexts_are_ignored(self):
        router = FileOpenRouter()
        handler = _Handler("invalid", priority=100, contexts=("view", "bogus"))
        default = _Handler("default")
        router.set_default(default)
        with self.assertLogs("ui.file_open_router", level="WARNING"):
            router.register(handler)

        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(handler.calls, [])
        self.assertEqual(default.calls, [Path("a.ltx")])

    def test_supports_context_error_is_isolated(self):
        class BrokenHandler(_Handler):
            def supports_context(self, context):
                raise RuntimeError("broken contexts")

        router = FileOpenRouter()
        broken = BrokenHandler("broken", priority=100)
        default = _Handler("default")
        router.set_default(default)
        router.register(broken)

        with self.assertLogs("ui.file_open_router", level="ERROR"):
            self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(broken.calls, [])
        self.assertEqual(default.calls, [Path("a.ltx")])

    def test_unknown_context_is_rejected(self):
        router = FileOpenRouter()
        with self.assertRaises(ValueError):
            router.open(Path("a.ltx"), self.api, context="unknown")

    def test_context_without_handler_uses_default(self):
        router = FileOpenRouter()
        view = _Handler("view", priority=100, contexts=("view",))
        default = _Handler("default")
        router.register(view)
        router.set_default(default)

        self.assertTrue(router.open(Path("a.ltx"), self.api, context="edit"))
        self.assertEqual(view.calls, [])
        self.assertEqual(default.calls, [Path("a.ltx")])

    def test_chooser_resolves_ambiguity_and_remembers_choice(self):
        router = FileOpenRouter()
        first = _Handler("first", priority=10, contexts=("view",))
        second = _Handler("second", priority=10, contexts=("view",))
        router.register(first, owner="mod_a")
        router.register(second, owner="mod_b")

        choices = []

        def chooser(path, context, options):
            choices.append((Path(path), context, options))
            return options[1][0]

        router.set_chooser(chooser)
        self.assertTrue(router.open(Path("a.ltx"), self.api, context="view"))
        self.assertEqual(first.calls, [])
        self.assertEqual(second.calls, [Path("a.ltx")])
        self.assertEqual(choices[0][1], "view")
        self.assertTrue(router.preference(".ltx", "view"))

        router.set_chooser(None)
        self.assertTrue(router.open(Path("a.ltx"), self.api, context="view"))
        self.assertEqual(first.calls, [])
        self.assertEqual(
            second.calls,
            [Path("a.ltx"), Path("a.ltx")],
        )

    def test_same_class_instances_get_distinct_preferences(self):
        router = FileOpenRouter()
        first = _Handler("first", priority=10, handler_id="first")
        second = _Handler("second", priority=10, handler_id="second")
        router.register(first, owner="mod_same")
        router.register(second, owner="mod_same")

        options = []

        def chooser(path, context, values):
            options.extend(values)
            return values[1][0]

        router.set_chooser(chooser)
        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(first.calls, [])
        self.assertEqual(second.calls, [Path("a.ltx")])
        self.assertEqual(len({key for key, _label in options}), 2)
        self.assertEqual(len({label for _key, label in options}), 2)

        router.set_chooser(None)
        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(first.calls, [])
        self.assertEqual(
            second.calls,
            [Path("a.ltx"), Path("a.ltx")],
        )

    def test_invalid_handler_id_is_ignored(self):
        router = FileOpenRouter()
        handler = _Handler(
            "invalid",
            priority=100,
            handler_id="bad#id",
        )
        default = _Handler("default")
        router.set_default(default)
        with self.assertLogs("ui.file_open_router", level="WARNING"):
            router.register(handler)

        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(handler.calls, [])
        self.assertEqual(default.calls, [Path("a.ltx")])

    def test_duplicate_handler_without_id_is_ignored(self):
        router = FileOpenRouter()
        first = _Handler("first", priority=10, handler_id="")
        second = _Handler("second", priority=10, handler_id="")
        router.register(first, owner="mod_same")
        with self.assertLogs("ui.file_open_router", level="WARNING"):
            router.register(second, owner="mod_same")

        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(first.calls, [Path("a.ltx")])
        self.assertEqual(second.calls, [])

    def test_stable_handler_id_survives_registration_order(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "file_open.json"
            first = _Handler("first", priority=10, handler_id="2")
            second = _Handler("second", priority=10, handler_id="3")
            router = FileOpenRouter(preferences_path=path)
            router.register(first, owner="mod_same")
            router.register(second, owner="mod_same")
            router.set_chooser(lambda p, c, options: options[1][0])
            self.assertTrue(router.open(Path("a.ltx"), self.api))
            self.assertEqual(second.calls, [Path("a.ltx")])

            restored = FileOpenRouter(preferences_path=path)
            first.calls = []
            second.calls = []
            restored.register(second, owner="mod_same")
            restored.register(first, owner="mod_same")
            self.assertTrue(restored.open(Path("a.ltx"), self.api))
            self.assertEqual(first.calls, [])
            self.assertEqual(second.calls, [Path("a.ltx")])

    def test_old_preference_version_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "file_open.json"
            # Старый порядковый ключ совпадает по форме с новым
            # handler_id="2"; файл версии 1 должен быть отброшен целиком.
            old_key = "mod_same|%s.%s#2" % (
                _Handler.__module__,
                _Handler.__qualname__,
            )
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "preferences": {"view|.ltx": old_key},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertLogs(
                "ui.platform.file_open_preferences",
                level="WARNING",
            ):
                router = FileOpenRouter(preferences_path=path)
            self.assertEqual(router.preference(".ltx", "view"), "")

            first = _Handler("first", priority=10, handler_id="2")
            second = _Handler("second", priority=10, handler_id="3")
            router.register(first, owner="mod_same")
            router.register(second, owner="mod_same")
            router.set_chooser(lambda p, c, options: options[1][0])
            self.assertTrue(router.open(Path("a.ltx"), self.api))
            self.assertEqual(first.calls, [])
            self.assertEqual(second.calls, [Path("a.ltx")])

    def test_preference_is_persisted(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "file_open.json"
            router = FileOpenRouter(preferences_path=path)
            first = _Handler("first", priority=10)
            second = _Handler("second", priority=10)
            router.register(first, owner="mod_a")
            router.register(second, owner="mod_b")
            router.set_chooser(lambda p, c, options: options[1][0])
            self.assertTrue(router.open(Path("a.ltx"), self.api))
            self.assertTrue(path.is_file())
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["version"],
                PREFERENCES_VERSION,
            )

            restored = FileOpenRouter(preferences_path=path)
            first.calls = []
            second.calls = []
            restored.register(first, owner="mod_a")
            restored.register(second, owner="mod_b")
            self.assertTrue(restored.open(Path("a.ltx"), self.api))
            self.assertEqual(first.calls, [])
            self.assertEqual(second.calls, [Path("a.ltx")])

    def test_preference_does_not_override_priority(self):
        router = FileOpenRouter()
        low = _Handler("low", priority=10)
        high = _Handler("high", priority=10)
        router.register(low, owner="mod_low")
        router.register(high, owner="mod_high")
        router.set_chooser(lambda p, c, options: options[0][0])
        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(low.calls, [Path("a.ltx")])
        self.assertEqual(high.calls, [])

        high.priority = 20
        self.assertTrue(router.open(Path("a.ltx"), self.api))
        self.assertEqual(low.calls, [Path("a.ltx")])
        self.assertEqual(high.calls, [Path("a.ltx")])


if __name__ == "__main__":
    unittest.main()

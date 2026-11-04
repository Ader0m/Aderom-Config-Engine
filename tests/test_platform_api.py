# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from sdk.events import Events
from ui.platform_api import PlatformApi


class FakeEvents:
    def __init__(self):
        self._subscribers = {}

    def subscribe(self, name, callback):
        self._subscribers.setdefault(name, []).append(callback)
        return None

    def publish(self, name, payload=None):
        for callback in self._subscribers.get(name, ()):
            callback(payload)


class FakeCommands:
    def __init__(self):
        self._handlers = {}

    def register(self, name, handler):
        self._handlers[name] = handler

    def execute(self, name, *args, **kwargs):
        return self._handlers[name](*args, **kwargs)


class FakeWidget:
    def __init__(self):
        self.lines = []

    def goto_line(self, line_number):
        self.lines.append(line_number)


class FakeEditor:
    def __init__(self):
        self.widget = FakeWidget()

    def currentWidget(self):
        return self.widget


class FakeStatusBar:
    def showMessage(self, *_args, **_kwargs):
        pass


class FakeWindow:
    def __init__(self):
        self.editor = FakeEditor()

    def log(self, _message):
        pass

    def log_status(self, _key, _message):
        pass

    def statusBar(self):
        return FakeStatusBar()


class PublishingHandler:
    """Имитация обработчика шины: публикует FILE_OPENED после открытия."""

    def __init__(self, events):
        self._events = events

    def open(self, path, api):
        api.events.publish(Events.FILE_OPENED, path)
        return True


class PlatformApiTest(unittest.TestCase):
    def test_open_at_line_applies_pending_navigation(self):
        events = FakeEvents()
        commands = FakeCommands()
        api = PlatformApi(events, commands, None, None, None)
        api.window = FakeWindow()
        api.router.set_default(PublishingHandler(events))

        path = Path("a.ltx")
        self.assertTrue(api.request_open_file_at_line(path, 42))

        self.assertEqual(api.window.editor.widget.lines, [42])
        self.assertEqual(api._pending_file_actions, {})

    def test_failed_open_cancels_pending_navigation(self):
        events = FakeEvents()
        commands = FakeCommands()
        api = PlatformApi(events, commands, None, None, None)
        api.window = FakeWindow()

        class FailingHandler:
            def open(self, path, api):
                api.events.publish(Events.FILE_OPEN_FAILED, path)
                return True

        api.router.set_default(FailingHandler())

        path = Path("a.ltx")
        self.assertTrue(api.request_open_file_at_line(path, 42))

        self.assertEqual(api.window.editor.widget.lines, [])
        self.assertEqual(api._pending_file_actions, {})


if __name__ == "__main__":
    unittest.main()

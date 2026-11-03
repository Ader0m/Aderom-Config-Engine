# -*- coding: utf-8 -*-
"""Открытие XML-файлов: параллельный LTX путь без резолва.

XML не участвует в проходе резолвера и не имеет #include/DLTX-патчей.
Поэтому у него только один режим просмотра — исходный текст файла.
"""

from pathlib import Path

from sdk import FileOpenHandler


class XmlFileOpener:
    """Открывает .xml как сырой текст, без каких-либо преобразований."""

    def __init__(self, ctx):
        self.ctx = ctx

    def open(self, path) -> bool:
        path = Path(path)

        self.ctx.ui.set_status("XML (без резолва): %s…" % path.name)
        self.ctx.ui.open_raw(path)
        self.ctx.ui.set_status("Открыт XML: %s" % path.name, 5000)
        return True


class RawXmlHandler(FileOpenHandler):
    """Перехватывает открытие .xml и делегирует его :class:`XmlFileOpener`."""

    priority = 200

    def __init__(self, opener: XmlFileOpener):
        self._opener = opener

    def can_open(self, path):
        return Path(path).suffix.lower() == ".xml"

    def open(self, path, api):
        return self._opener.open(path)

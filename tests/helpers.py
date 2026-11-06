# -*- coding: utf-8 -*-
"""Общие помощники тестов.

Здесь намеренно нет импортов из ``core``/``ui``: помощники должны быть
пригодны и для модульных тестов, и для тестов границ слоёв.
"""

from pathlib import Path

from tests.support import SRC  # noqa: F401


DEFAULT_ENCODING = "windows-1251"


def write_text(path, text, encoding=DEFAULT_ENCODING):
    """Пишет текст с предсказуемыми LF-переводами строк."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    path.write_bytes(normalized.encode(encoding))
    return path


def read_text(path, encoding=DEFAULT_ENCODING):
    """Читает текст и нормализует переводы строк для сравнений."""
    return Path(path).read_text(encoding=encoding).replace("\r\n", "\n")


def touch_after(path, seconds=2):
    """Сдвигает mtime файла вперёд, не меняя содержимое.

    Нужен для проверок инвалидизации кэшей на файловых системах с грубым
    разрешением времени. ``ctime`` на Windows меняется при записи, но
    mtime-сдвиг делает тест устойчивее.
    """
    import os

    path = Path(path)
    stat = path.stat()
    os.utime(
        path,
        ns=(
            stat.st_atime_ns,
            stat.st_mtime_ns + int(seconds * 1_000_000_000),
        ),
    )


class FakeForest:
    """Минимальный объект леса для тестов, не зависящий от TreeBuilder."""

    def __init__(
        self,
        scan_dir,
        files,
        parents=None,
        xml_files=(),
        include_children=None,
        dltx_attachments=None,
    ):
        self.scan_dir = str(scan_dir)
        self.files = [Path(path) for path in files]
        self.roots = list(self.files)
        self.rel_paths = {path: path.name for path in self.files}
        self.display_children = {}
        self.subtree_counts = {path: 1 for path in self.files}
        self.system_root = str(self.files[0]) if self.files else ""
        self.total_files = len(self.files)
        self.system_tree_files = len(self.files)
        self.xml_files = [Path(path) for path in xml_files]
        self.total_xml_files = len(self.xml_files)
        self.include_parents = {
            Path(key): {Path(value) for value in values}
            for key, values in (parents or {}).items()
        }
        self.include_children = {
            Path(key): {Path(value) for value in values}
            for key, values in (include_children or {}).items()
        }
        self.dltx_attachments = {
            Path(key): {Path(value) for value in values}
            for key, values in (dltx_attachments or {}).items()
        }
        self.real_paths = {
            path: str(path) for path in self.files + self.xml_files
        }
        self.node_parent = {}

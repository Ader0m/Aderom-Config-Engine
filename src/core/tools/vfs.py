# -*- coding: utf-8 -*-
"""Разрешение VFS-пути в физический путь для внешних приложений.

Внутри приложения VFS-путь (MO2/usvfs, junction-сборка) читается
нормально, но Windows Shell запускает дочерний процесс, который может
не видеть виртуальные файлы. Для открытия .dds/.ogf нужен реальный
путь на диске.

Порядок разрешения:
  1. ``GetFinalPathNameByHandleW`` — самый точный способ на Windows;
  2. ``Path.resolve(strict=True)`` — junction/symlink-сборка VFS;
  3. временная копия — если файл доступен только через usvfs.

Кэш хранит результат на время жизни процесса: ключ включает
mtime/size, поэтому изменение файла сбрасывает запись.
"""

import ctypes
import hashlib
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Optional

from core.tools.fs import normalize_path


_CACHE = {}
_CACHE_LOCK = threading.Lock()

# Windows API
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_FILE_READ_ATTRIBUTES = 0x0080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000


def _same_path(a, b) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _strip_extended_prefix(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _win_path(path: Path) -> str:
    """Путь для CreateFileW, при необходимости в extended-форме."""
    value = str(path)
    if len(value) < 248 or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value.lstrip("\\")
    return "\\\\?\\" + value


def _final_path_by_handle(path: Path) -> Optional[Path]:
    """Реальный путь по открытому handle (Windows)."""
    if os.name != "nt":
        return None

    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE

        get_final_path = kernel32.GetFinalPathNameByHandleW
        get_final_path.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        get_final_path.restype = wintypes.DWORD

        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = create_file(
            _win_path(path),
            _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if not handle or handle == _INVALID_HANDLE_VALUE:
            return None

        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = get_final_path(handle, buffer, len(buffer), 0)
            if length <= 0 or length >= len(buffer):
                return None
            return Path(_strip_extended_prefix(buffer.value))
        finally:
            close_handle(handle)
    except Exception:
        return None


def _resolve_links(path: Path) -> Optional[Path]:
    """Путь после раскрытия junction/symlink."""
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def _usvfs_active() -> bool:
    """Запущен ли процесс с хуками MO2/usvfs."""
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        for name in ("usvfs_x64.dll", "usvfs_x86.dll"):
            if kernel32.GetModuleHandleW(name):
                return True
    except Exception:
        pass
    return False


def _materialize(path: Path) -> Optional[Path]:
    """Скопировать VFS-файл во временный реальный каталог."""
    try:
        stat = path.stat()
    except OSError:
        return None

    key = "%s|%s|%s" % (path, stat.st_mtime_ns, stat.st_size)
    digest = hashlib.sha1(
        key.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:16]

    target_dir = Path(tempfile.gettempdir()) / "ace_assets" / digest
    target = target_dir / path.name

    try:
        if not target.is_file() or target.stat().st_size != stat.st_size:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        return target
    except OSError:
        return None


def _resolve_uncached(path: Path, materialize: Optional[bool]):
    original = normalize_path(path)

    real = _final_path_by_handle(original)
    if real is not None and not _same_path(real, original):
        return normalize_path(real)

    real = _resolve_links(original)
    if real is not None and not _same_path(real, original):
        return normalize_path(real)

    if materialize is None:
        materialize = _usvfs_active()

    if materialize:
        copy_path = _materialize(original)
        if copy_path is not None:
            return normalize_path(copy_path)

    return original


def resolve_asset_path(scan_dir, kind: str, raw_ref):
    """Полный VFS-путь к текстуре (``.dds``) или модели (``.ogf``).

    ``scan_dir`` — каталог configs игры (``<root>/gamedata/configs``).
    Ресурсы лежат рядом с ним: ``textures`` и ``meshes``. Проверяем
    несколько баз, чтобы покрыть и VFS-корень, и отдельный каталог мода.
    """
    if not scan_dir:
        return None

    raw = str(raw_ref or "").strip().strip('"').replace("\\", "/")
    if not raw:
        return None

    suffix = ".dds" if kind == "texture" else ".ogf"
    subdir = "textures" if kind == "texture" else "meshes"

    p = Path(raw)
    if p.is_absolute():
        candidates = [p]
    else:
        if not p.suffix:
            raw += suffix
        has_subdir = raw.lower().startswith(subdir + "/")

        bases = []
        scan = Path(scan_dir)
        for base in (scan, scan.parent, scan.parent.parent):
            for candidate_base in (base, base / "gamedata"):
                if candidate_base not in bases:
                    bases.append(candidate_base)

        candidates = []
        for base in bases:
            if has_subdir:
                candidates.append(base / raw)
            else:
                candidates.append(base / subdir / raw)
                candidates.append(base / raw)

    for candidate in candidates:
        try:
            if candidate.is_file():
                return normalize_path(candidate)
        except OSError:
            continue

    return None


def resolve_physical_path(path, materialize: Optional[bool] = None):
    """Вернуть реальный путь для запуска внешней программы.

    ``None`` — файл не существует.
    ``materialize=None`` — временная копия создаётся только при
    активном usvfs, если реальный путь не удалось получить иначе.
    ``materialize=True`` — форсировать копию (используется в тестах).
    """
    if path is None:
        return None

    original = normalize_path(path)
    try:
        stat = original.stat()
    except OSError:
        return None

    key = (str(original), stat.st_mtime_ns, stat.st_size, materialize)
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]

    result = _resolve_uncached(original, materialize)

    with _CACHE_LOCK:
        _CACHE[key] = result
    return result


def clear_cache():
    """Сбросить кэш разрешённых путей."""
    with _CACHE_LOCK:
        _CACHE.clear()

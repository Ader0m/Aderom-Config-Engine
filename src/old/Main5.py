#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Main5.py

Проводник-подобная файловая система LTX (аналог Windows Explorer):
- "Этот компьютер" -> узел папки сканирования -> корни LTX -> дети.
- Иконки: компьютер, диск, папка закрытая/открытая, файл.
- Анимированный круг (спиннер) рядом с именем файла, пока resolver4
  собирает файл. Прогресс не отслеживается, круг просто крутится.
- Двойной клик / Enter на родителе: сборка через resolver4 и сразу
  открытие собранного файла в Notepad++.
- Двойной клик / Enter на ребёнке: просмотр детей недоступен.
- Сортировка столбцов, поиск, диагностика, сохранение/копирование.
"""

import os
import sys
import re
import math
import zlib
import struct
import base64
import threading
import traceback
import importlib.util
import subprocess
import shutil
from pathlib import Path
from collections import defaultdict

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext


SCRIPT_DIR = Path(__file__).resolve().parent

DEFAULT_GAME_ROOT = r"H:\GRA2.5\Anomaly 1.5.3 up"
DEFAULT_CONFIG_DIR = r"H:\GRA2.5\Anomaly 1.5.3 up\gamedata\configs"

GENERATED_DIR = SCRIPT_DIR / "generated"

RESOLVER_CANDIDATES = (
    "Resolver4.py",
    "resolver4.py",
)

SPIN_FRAMES = 12
TICK_MS = 80


# ============================================================
# Утилиты путей / масок / чтения
# ============================================================

def normalize_path(p):
    return Path(os.path.normpath(str(p))).resolve()


def rel_path(p, base):
    try:
        return os.path.relpath(str(p), str(base))
    except ValueError:
        return str(p)


def pattern_match(name, mask):
    rx = "^" + "".join(
        ".*" if c == "*" else "." if c == "?" else re.escape(c)
        for c in str(mask).lower()
    ) + "$"
    return re.match(rx, str(name).lower()) is not None


def read_text_safe(path):
    raw = Path(path).read_bytes()

    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("windows-1251", errors="replace")


def strip_comments(line):
    in_quotes = False
    i = 0
    n = len(line)

    while i < n:
        ch = line[i]

        if ch == '"':
            in_quotes = not in_quotes

        elif not in_quotes:
            if ch == ";":
                return line[:i].strip()

            if ch == "/" and i + 1 < n and line[i + 1] == "/":
                return line[:i].strip()

        i += 1

    return line.strip()


INCLUDE_QUOTED_RE = re.compile(r'#include\s+"([^"]+)"', re.IGNORECASE)
INCLUDE_BARE_RE = re.compile(r'#include\s+([^\s;]+)', re.IGNORECASE)


def extract_includes(text):
    result = []

    for line in text.splitlines():
        s = strip_comments(line)

        if not s:
            continue

        if s.startswith("#") and "include" in s.lower():
            m = INCLUDE_QUOTED_RE.search(s)

            if m:
                result.append(m.group(1))
            else:
                m = INCLUDE_BARE_RE.search(s)
                if m:
                    result.append(m.group(1).strip('"'))

    return result


def resolve_include_targets(inc_name, parent_path):
    inc = str(inc_name or "").strip().strip('"').replace("\\", "/")

    if not inc:
        return []

    parent_dir = Path(parent_path).parent
    p = Path(inc)

    if p.is_absolute():
        if "*" in inc or "?" in inc:
            search_dir = p.parent
            mask = p.name

            if not search_dir.is_dir():
                return []

            out = []
            for entry in search_dir.iterdir():
                if (
                    entry.is_file()
                    and entry.suffix.lower() == ".ltx"
                    and pattern_match(entry.name.lower(), mask.lower())
                ):
                    out.append(normalize_path(entry))

            return sorted(out, key=lambda x: str(x).lower())

        full = normalize_path(p)
        return [full] if full.is_file() else []

    inc_rel = inc.lstrip("/")

    if "*" in inc_rel or "?" in inc_rel:
        parts = inc_rel.split("/")
        mask = parts[-1]
        subdir = "/".join(parts[:-1])

        search_dir = parent_dir / subdir if subdir else parent_dir

        if not search_dir.is_dir():
            return []

        out = []
        for entry in search_dir.iterdir():
            if (
                entry.is_file()
                and entry.suffix.lower() == ".ltx"
                and pattern_match(entry.name.lower(), mask.lower())
            ):
                out.append(normalize_path(entry))

        return sorted(out, key=lambda x: str(x).lower())

    full = normalize_path(parent_dir / inc_rel)

    if full.is_file():
        return [full]

    alt = normalize_path(parent_dir / inc)
    if alt.is_file():
        return [alt]

    return []


def scan_ltx_files(root_dir):
    root = Path(root_dir)
    out = set()

    try:
        for p in root.rglob("*"):
            try:
                if p.is_file() and p.suffix.lower() == ".ltx":
                    out.add(normalize_path(p))
            except OSError:
                pass
    except Exception:
        pass

    return sorted(out, key=lambda x: str(x).lower())


def build_search_bases(scan_dir, selected_dir):
    bases = []

    for d in (scan_dir, selected_dir, SCRIPT_DIR, Path.cwd()):
        if not d:
            continue

        p = Path(d)

        if p not in bases:
            bases.append(p)

        try:
            if p.parent not in bases:
                bases.append(p.parent)
        except Exception:
            pass

    return bases


def find_resolver_script(bases):
    for base in bases:
        for name in RESOLVER_CANDIDATES:
            p = Path(base) / name
            if p.is_file():
                return normalize_path(p)

    for name in RESOLVER_CANDIDATES:
        stem = Path(name).stem

        try:
            spec = importlib.util.find_spec(stem)
            if spec and spec.origin and Path(spec.origin).is_file():
                return normalize_path(spec.origin)
        except Exception:
            pass

    return None


def load_resolver_module(path):
    p = Path(path)

    if not p.is_file():
        raise FileNotFoundError(f"Resolver не найден: {path}")

    spec = importlib.util.spec_from_file_location(p.stem, str(p))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[p.stem] = mod
    spec.loader.exec_module(mod)

    return mod


def find_system_ltx(selected_dir):
    sel = Path(selected_dir)

    candidates = [
        sel / "system.ltx",
        sel / "gamedata" / "configs" / "system.ltx",
        sel / "configs" / "system.ltx",
    ]

    for c in candidates:
        if c.is_file():
            return normalize_path(c)

    for p in sel.rglob("system.ltx"):
        if p.is_file():
            return normalize_path(p)

    return None


def get_dltx_mods_for_root(root_path, folder_files):
    root_path = Path(root_path)
    stem = root_path.stem.lower()

    ambiguous = {
        f.stem.lower()
        for f in folder_files
        if f.stem.lower().startswith(stem + "_")
    }

    mods = []

    for f in folder_files:
        name = f.name.lower()

        if not name.startswith("mod_" + stem + "_"):
            continue

        skip = False

        for amb in ambiguous:
            if amb == stem:
                continue

            head = "mod_" + amb + "_"
            pat = "mod_" + amb + "_*.ltx"

            if (
                name.startswith(head)
                and len(name) > len(head) + 4
                and pattern_match(name, pat)
            ):
                skip = True
                break

        if not skip:
            mods.append(f)

    return sorted(mods, key=lambda x: x.name.lower())


def open_in_notepad_pp(path):
    candidates = []

    exe = shutil.which("notepad++")
    if exe:
        candidates.append(exe)

    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "Notepad++", "notepad++.exe"))

    for c in candidates:
        try:
            if c and Path(c).is_file():
                subprocess.Popen([str(c), str(path)])
                return True, ""
        except Exception:
            pass

    return False, (
        "Notepad++ не найден.\n"
        "Проверьте, установлен ли он и доступен ли как notepad++ "
        "или в папке Program Files\\Notepad++."
    )


# ============================================================
# Построение леса LTX (как в Main4)
# ============================================================

def build_ltx_forest(scan_dir, system_root, log):
    files = scan_ltx_files(scan_dir)
    file_set = set(files)

    rel_paths = {f: rel_path(f, scan_dir) for f in files}

    by_folder = defaultdict(list)
    for f in files:
        by_folder[f.parent].append(f)

    log(f"Найдено .ltx файлов: {len(files)}")

    raw_children = defaultdict(set)
    raw_parents = defaultdict(set)
    missing = []

    for parent in files:
        try:
            text = read_text_safe(parent)
        except Exception as e:
            log(f"Не удалось прочитать файл: {parent}")
            log(str(e))
            continue

        for inc in extract_includes(text):
            targets = resolve_include_targets(inc, parent)

            if not targets:
                missing.append((parent, inc))
                continue

            for child in targets:
                if child == parent:
                    continue

                if child not in file_set:
                    missing.append((parent, inc))
                    continue

                raw_children[parent].add(child)
                raw_parents[child].add(parent)

    include_edges = sum(len(v) for v in raw_children.values())
    log(f"Построено include-связей: {include_edges}")
    log(f"Пропавших/внешних include: {len(missing)}")

    if missing:
        log("Примеры пропавших include:")
        for parent, inc in missing[:20]:
            log(f"  {rel_paths.get(parent, str(parent))} -> {inc}")

    provisional_roots = [f for f in files if not raw_parents.get(f)]

    attached_mods = 0

    for root in provisional_roots:
        folder_files = by_folder.get(root.parent, [])

        for mod in get_dltx_mods_for_root(root, folder_files):
            if mod == root:
                continue

            if not raw_parents.get(mod):
                raw_children[root].add(mod)
                raw_parents[mod].add(root)
                attached_mods += 1

    log(f"Подключено DLTX mod_*.ltx к корням без include-родителя: {attached_mods}")

    final_roots = [f for f in files if not raw_parents.get(f)]

    if system_root and system_root in file_set and system_root not in final_roots:
        final_roots.append(system_root)

    final_roots.sort(
        key=lambda p: (
            0 if p == system_root else 1,
            rel_paths.get(p, str(p)).lower(),
        )
    )

    display_children = defaultdict(list)
    node_parent = {}
    tree_root = {}
    tree_files = defaultdict(list)

    def assign(root):
        stack = [(root, None)]

        while stack:
            node, parent = stack.pop()

            if node in tree_root:
                continue

            tree_root[node] = root
            tree_files[root].append(node)

            if parent is not None:
                node_parent[node] = parent
                display_children[parent].append(node)

            child_items = sorted(
                raw_children.get(node, set()),
                key=lambda x: rel_paths.get(x, str(x)).lower(),
                reverse=True,
            )

            for ch in child_items:
                if ch not in tree_root:
                    stack.append((ch, node))

    for r in final_roots:
        assign(r)

    for node in files:
        if node not in tree_root:
            final_roots.append(node)
            assign(node)

    for p in list(display_children.keys()):
        display_children[p].sort(key=lambda x: rel_paths.get(x, str(x)).lower())

    actual_roots = [r for r in final_roots if tree_root.get(r) == r]

    actual_roots.sort(
        key=lambda x: (
            0 if x == system_root else 1,
            rel_paths.get(x, str(x)).lower(),
        )
    )

    subtree_counts = {}

    for root in actual_roots:
        stack = [(root, False)]

        while stack:
            node, visited = stack.pop()

            if visited:
                total = 1

                for ch in display_children.get(node, []):
                    total += subtree_counts.get(ch, 1)

                subtree_counts[node] = total
            else:
                if node in subtree_counts:
                    continue

                stack.append((node, True))

                for ch in display_children.get(node, []):
                    if ch not in subtree_counts:
                        stack.append((ch, False))

    system_tree_files = 0
    if system_root:
        system_tree_files = len(tree_files.get(system_root, []))

    return {
        "scan_dir": str(scan_dir),
        "system_root": str(system_root) if system_root else "",
        "total_files": len(files),
        "roots": actual_roots,
        "roots_set": set(actual_roots),
        "display_children": display_children,
        "node_parent": node_parent,
        "subtree_counts": subtree_counts,
        "rel_paths": rel_paths,
        "files": files,
        "system_tree_files": system_tree_files,
    }


# ============================================================
# Генерация иконок и спиннера (PNG с прозрачностью, без PIL)
# ============================================================

def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _png_chunk(tag, data):
    chunk = struct.pack(">I", len(data)) + tag + data
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return chunk + struct.pack(">I", crc)


def _make_png(width, height, rows):
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", ihdr)
    png += _png_chunk(b"IDAT", zlib.compress(raw, 6))
    png += _png_chunk(b"IEND", b"")
    return base64.b64encode(png)


class PixBuf:
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.rows = [bytearray(w * 4) for _ in range(h)]

    def put(self, x, y, r, g, b, a=255):
        if 0 <= x < self.w and 0 <= y < self.h:
            row = self.rows[y]
            o = x * 4
            row[o] = r
            row[o + 1] = g
            row[o + 2] = b
            row[o + 3] = a


def _blit(buf, x0, y0, rows, pal):
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            c = pal.get(ch)
            if c:
                r, g, b = _hex(c)
                buf.put(x0 + x, y0 + y, r, g, b)


def _draw_spinner(buf, x0, y0, k):
    cx = x0 + 7.5
    cy = y0 + 7.5
    start = k * 2 * math.pi / SPIN_FRAMES

    for y in range(y0, y0 + 16):
        for x in range(x0, x0 + 16):
            dx = x - cx
            dy = y - cy
            r = math.hypot(dx, dy)

            if 4.6 <= r <= 7.2:
                ang = math.atan2(dy, dx) % (2 * math.pi)
                diff = (ang - start) % (2 * math.pi)

                if diff < math.pi * 0.5:
                    col = (40, 120, 220)
                elif diff < math.pi * 0.8:
                    col = (120, 170, 230)
                else:
                    col = (200, 210, 220)

                buf.put(x, y, *col)


ICON_DEFS = {
    "computer": (
        [
            "................",
            "..DDDDDDDDDDDD..",
            "..DBBBBBBBBBBD..",
            "..DBBBBBBBBBBD..",
            "..DBBBBBBBBBBD..",
            "..DBBBBBBBBBBD..",
            "..DBBBBBBBBBBD..",
            "..DBBBBBBBBBBD..",
            "..DDDDDDDDDDDD..",
            ".......DD.......",
            ".......DD.......",
            "....DDDDDDDD....",
            "....DDDDDDDD....",
            "................",
            "................",
            "................",
        ],
        {"D": "#4a5568", "B": "#7fb2e5"},
    ),
    "drive": (
        [
            "................",
            "................",
            "................",
            ".DDDDDDDDDDDDDD.",
            ".DGGGGGGGGGGGGD.",
            ".DGGGGGGGGGGGYD.",
            ".DGGGGGGGGGGGGD.",
            ".DDDDDDDDDDDDDD.",
            "................",
            "................",
            "................",
            "................",
            "................",
            "................",
            "................",
            "................",
        ],
        {"D": "#4a5568", "G": "#cfd6dd", "Y": "#e5b53a"},
    ),
    "folder": (
        [
            "................",
            "................",
            ".DDDDDD.........",
            ".DLLLLDDDDDDDDD.",
            ".DLLLLLLLLLLLLD.",
            ".DYYYYYYYYYYYYD.",
            ".DYYYYYYYYYYYYD.",
            ".DYYYYYYYYYYYYD.",
            ".DYYYYYYYYYYYYD.",
            ".DYYYYYYYYYYYYD.",
            ".DYYYYYYYYYYYYD.",
            ".DYYYYYYYYYYYYD.",
            ".DDDDDDDDDDDDDD.",
            "................",
            "................",
            "................",
        ],
        {"D": "#8a6a1f", "L": "#f6e3a1", "Y": "#e9c463"},
    ),
    "folder_open": (
        [
            "................",
            "................",
            ".DDDDDD.........",
            ".DLLLLDDDDDDDDD.",
            ".DLLLLLLLLLLLLD.",
            ".DYYYYYYYYYYYYD.",
            ".DYYYYYYYYYYYYD.",
            ".DDDDDDDDDDDDDD.",
            ".DLLLLLLLLLLLLD.",
            ".DLLLLLLLLLLLLD.",
            ".DLLLLLLLLLLLLD.",
            ".DLLLLLLLLLLLLD.",
            ".DDDDDDDDDDDDDD.",
            "................",
            "................",
            "................",
        ],
        {"D": "#8a6a1f", "L": "#f6e3a1", "Y": "#e9c463"},
    ),
    "file": (
        [
            "................",
            "................",
            "...DDDDDDDDDD...",
            "...DWWWWWWWWD...",
            "...DWWWWWWWWD...",
            "...DWGWGWGWWD...",
            "...DWWWWWWWWD...",
            "...DWGWGWGWWD...",
            "...DWWWWWWWWD...",
            "...DWGWGWGWWD...",
            "...DWWWWWWWWD...",
            "...DWWWWWWWWD...",
            "...DDDDDDDDDD...",
            "................",
            "................",
            "................",
        ],
        {"D": "#5a6470", "W": "#ffffff", "G": "#9aa5b1"},
    ),
}


# ============================================================
# UI
# ============================================================

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Main5 — проводник LTX")
        self.root.geometry("1380x900")

        self.config_dir = self._init_config_dir()
        self.cache = None

        self.sort_col = "path"
        self.sort_reverse = False

        self.iid_counter = 0
        self.iid_to_path = {}
        self.path_to_iid = {}
        self.populated_paths = set()
        self.item_type = {}

        self.computer_iid = None
        self.drive_iid = None

        self.spin_frame = 0
        self.busy_items = {}
        self.busy_count = {}

        self.search_matches = []
        self.search_index = 0
        self.last_search = ""

        self.resolver_mod = None
        self.source_map = None

        self.status_var = tk.StringVar(value="Выберите папку и нажмите Получить.")
        self.search_var = tk.StringVar(value="")

        self._icon_refs = []
        self.idle_img = {}
        self.busy_img = {}
        self._build_icons()

        self._build_ui()

        self.root.after(TICK_MS, self._tick)

    # ------------------------------------------------------------
    # Иконки
    # ------------------------------------------------------------

    def _build_icons(self):
        for name, (rows, pal) in ICON_DEFS.items():
            idle_buf = PixBuf(32, 16)
            _blit(idle_buf, 0, 0, rows, pal)
            self.idle_img[name] = self._photo(idle_buf)

            frames = []
            for k in range(SPIN_FRAMES):
                b = PixBuf(32, 16)
                _blit(b, 0, 0, rows, pal)
                _draw_spinner(b, 16, 0, k)
                frames.append(self._photo(b))

            self.busy_img[name] = frames

    def _photo(self, buf):
        img = tk.PhotoImage(data=_make_png(buf.w, buf.h, buf.rows))
        self._icon_refs.append(img)
        return img

    def _set_icon(self, iid, type_name):
        self.item_type[iid] = type_name

        if iid in self.busy_items:
            self.busy_items[iid] = type_name
            img = self.busy_img[type_name][self.spin_frame]
        else:
            img = self.idle_img[type_name]

        self.tree.item(iid, image=img)

    def _tick(self):
        self.spin_frame = (self.spin_frame + 1) % SPIN_FRAMES

        if self.busy_items:
            for iid, typ in list(self.busy_items.items()):
                try:
                    if self.tree.exists(iid):
                        self.tree.item(iid, image=self.busy_img[typ][self.spin_frame])
                except tk.TclError:
                    pass

        self.root.after(TICK_MS, self._tick)

    def set_busy(self, iid, busy=True):
        typ = self.item_type.get(iid)

        if not typ:
            return

        if busy:
            self.busy_count[iid] = self.busy_count.get(iid, 0) + 1
            self.busy_items[iid] = typ
            self.tree.item(iid, image=self.busy_img[typ][self.spin_frame])
        else:
            c = self.busy_count.get(iid, 0) - 1

            if c <= 0:
                self.busy_count.pop(iid, None)
                self.busy_items.pop(iid, None)
                self.tree.item(iid, image=self.idle_img[typ])
            else:
                self.busy_count[iid] = c

    # ------------------------------------------------------------
    # Прочее
    # ------------------------------------------------------------

    def _init_config_dir(self):
        candidates = [
            Path(DEFAULT_CONFIG_DIR),
            Path(DEFAULT_GAME_ROOT) / "gamedata" / "configs",
            Path(DEFAULT_GAME_ROOT) / "configs",
            Path(DEFAULT_GAME_ROOT),
        ]

        for c in candidates:
            try:
                if c.is_dir():
                    return c
            except OSError:
                pass

        return None

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=5)
        main.pack(fill="both", expand=True)

        status = ttk.Label(
            main,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
        )
        status.pack(side="bottom", fill="x", pady=(4, 0))

        top = ttk.Frame(main)
        top.pack(fill="x", pady=(0, 4))

        ttk.Button(
            top,
            text="Выбрать папку с LTX",
            command=self.choose_dir,
        ).pack(side="left", padx=(0, 4))

        self.dir_label = ttk.Label(
            top,
            text=str(self.config_dir or "<папка не выбрана>"),
            anchor="w",
        )
        self.dir_label.pack(side="left", fill="x", expand=True)

        btns = ttk.Frame(main)
        btns.pack(fill="x", pady=(0, 4))

        self.get_btn = ttk.Button(
            btns,
            text="Получить",
            command=self.on_get,
        )
        self.get_btn.pack(side="left", padx=(0, 4))

        ttk.Button(
            btns,
            text="Сохранить",
            command=self.save,
        ).pack(side="left", padx=(0, 4))

        ttk.Button(
            btns,
            text="Копировать",
            command=self.copy,
        ).pack(side="left", padx=(0, 12))

        ttk.Label(btns, text="Поиск файла:").pack(side="left", padx=(0, 4))

        search_entry = ttk.Entry(
            btns,
            textvariable=self.search_var,
            width=45,
        )
        search_entry.pack(side="left", padx=(0, 4))
        search_entry.bind("<Return>", self.on_search)

        ttk.Button(
            btns,
            text="Найти",
            command=self.on_search,
        ).pack(side="left")

        self.nb = ttk.Notebook(main)
        self.nb.pack(fill="both", expand=True)

        tab_tree = ttk.Frame(self.nb)
        tab_diag = ttk.Frame(self.nb)

        self.nb.add(tab_tree, text="Дерево LTX")
        self.nb.add(tab_diag, text="Диагностика")

        tree_frame = ttk.Frame(tab_tree)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("files", "children"),
            show="tree headings",
            selectmode="browse",
        )

        self.tree.heading(
            "#0",
            text="Имя",
            command=lambda: self.on_sort("path"),
        )
        self.tree.heading(
            "files",
            text="Файлов в дереве",
            command=lambda: self.on_sort("files"),
        )
        self.tree.heading(
            "children",
            text="Прямых детей",
            command=lambda: self.on_sort("children"),
        )

        self.tree.column("#0", width=880, anchor="w", stretch=True)
        self.tree.column("files", width=170, anchor="center", stretch=False)
        self.tree.column("children", width=170, anchor="center", stretch=False)

        ysb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        xsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscroll=ysb.set, xscroll=xsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")

        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewOpen>>", self.on_tree_open)
        self.tree.bind("<<TreeviewClose>>", self.on_tree_close)
        self.tree.bind("<Double-1>", self.on_tree_activate)
        self.tree.bind("<Return>", self.on_tree_activate)

        self.txt_diag = scrolledtext.ScrolledText(
            tab_diag,
            wrap="none",
            undo=False,
        )
        self.txt_diag.pack(fill="both", expand=True)
        self.txt_diag.configure(state="disabled")

        self._update_headings()

    # ------------------------------------------------------------
    # Лог / статус
    # ------------------------------------------------------------

    def log(self, msg):
        self.root.after(0, self._append_log_gui, str(msg))

    def _append_log_gui(self, msg):
        self.txt_diag.configure(state="normal")
        self.txt_diag.insert("end", msg + "\n")
        self.txt_diag.see("end")
        self.txt_diag.configure(state="disabled")

    def set_status(self, msg):
        self.root.after(0, lambda: self.status_var.set(str(msg)))

    # ------------------------------------------------------------
    # Выбор папки / запуск
    # ------------------------------------------------------------

    def choose_dir(self):
        initial = str(self.config_dir or DEFAULT_CONFIG_DIR or ".")

        d = filedialog.askdirectory(
            title="Выберите папку с LTX файлами",
            initialdir=initial,
        )

        if d:
            self.config_dir = Path(d)
            self.dir_label.config(text=str(self.config_dir))
            self.cache = None
            self.log(f"Выбрана папка: {d}")

    def on_get(self):
        if not self.config_dir or not Path(self.config_dir).is_dir():
            self.choose_dir()

            if not self.config_dir or not Path(self.config_dir).is_dir():
                return

        self.get_btn.config(state="disabled")
        self.set_status("Выполняется...")

        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            self.log("=" * 80)
            self.log(f"CWD: {os.getcwd()}")
            self.log(f"Script dir: {SCRIPT_DIR}")
            self.log(f"Выбранная папка: {self.config_dir}")

            selected_dir = Path(self.config_dir)

            system_ltx = find_system_ltx(selected_dir)

            if system_ltx is None:
                raise RuntimeError(
                    "Не найден System.ltx.\n"
                    "Выберите папку, где лежит system.ltx,\n"
                    "либо папку игры, где есть gamedata/configs/system.ltx."
                )

            self.log(f"Найден System.ltx: {system_ltx}")

            if (selected_dir / "system.ltx").is_file():
                scan_dir = selected_dir
            else:
                scan_dir = system_ltx.parent

            self.log(f"Папка сканирования: {scan_dir}")

            forest = build_ltx_forest(scan_dir, system_ltx, self.log)

            self.log(f"Всего .ltx файлов: {forest['total_files']}")
            self.log(f"Корней верхнего уровня: {len(forest['roots'])}")
            self.log(f"Файлов в дереве System.ltx: {forest['system_tree_files']}")

            self.cache = forest
            self.cache["resolver_sections"] = None
            self.source_map = None

            self.search_matches = []
            self.search_index = 0
            self.last_search = ""

            self.root.after(0, self.rebuild_tree)
            self.root.after(0, self.start_system_resolver_pass)

            self.set_status(
                f"Готово. Корней: {len(forest['roots'])}; "
                f"файлов в дереве System.ltx: {forest['system_tree_files']}"
            )

        except Exception as e:
            self.log(traceback.format_exc())
            msg = str(e)

            self.root.after(
                0,
                lambda m=msg: messagebox.showerror(
                    "Ошибка",
                    m + "\nПодробности во вкладке Диагностика.",
                ),
            )

            self.set_status("Ошибка.")

        finally:
            self.root.after(0, lambda: self.get_btn.config(state="normal"))

    # ------------------------------------------------------------
    # resolver4: фоновые проходы со спиннером
    # ------------------------------------------------------------

    def get_resolver_module(self):
        if self.resolver_mod is not None:
            return self.resolver_mod

        bases = []

        if self.cache:
            scan_dir = Path(self.cache["scan_dir"])
            selected_dir = self.config_dir or scan_dir
            bases = build_search_bases(scan_dir, selected_dir)
        else:
            bases = [SCRIPT_DIR, Path.cwd()]

        resolver_script = find_resolver_script(bases)

        if resolver_script is None:
            raise FileNotFoundError(
                "resolver4.py не найден.\n"
                "Положите resolver4.py рядом с Main5.py."
            )

        self.resolver_mod = load_resolver_module(resolver_script)
        return self.resolver_mod

    def start_system_resolver_pass(self):
        if not self.cache:
            return

        sys_str = self.cache.get("system_root", "")

        if not sys_str:
            return

        p = Path(sys_str)
        iid = self.path_to_iid.get(p)

        if not iid:
            return

        self.set_busy(iid, True)

        threading.Thread(
            target=self._system_pass_worker,
            args=(p, iid),
            daemon=True,
        ).start()

    def _system_pass_worker(self, p, iid):
        try:
            mod = self.get_resolver_module()
            cls = getattr(mod, "LtxResolver", None)

            if cls is None:
                raise RuntimeError("В resolver4.py не найден класс LtxResolver")

            try:
                resolver = cls(str(p), use_cache=False, verbose=False)
            except TypeError:
                resolver = cls(str(p), verbose=False)

            self.log(f"resolver4 pass: {p}")
            resolver.load()

            n = len(getattr(resolver, "data", []))
            self.cache["resolver_sections"] = n
            self.log(f"resolver4 pass готово. Секций: {n}")

        except Exception:
            self.log(traceback.format_exc())

        finally:
            self.root.after(0, lambda: self.set_busy(iid, False))

    def open_assembled_in_notepad(self, root_path):
        iid = self.path_to_iid.get(root_path)

        if iid:
            self.set_busy(iid, True)

        self.set_status(f"Собираю через resolver4: {root_path.name} ...")

        threading.Thread(
            target=self._open_assembled_worker,
            args=(root_path, iid),
            daemon=True,
        ).start()

    def _open_assembled_worker(self, root_path, iid):
        try:
            out_path = self.generate_assembled_file(root_path)

            ok, msg = open_in_notepad_pp(out_path)

            if not ok:
                self.root.after(
                    0,
                    lambda m=msg: messagebox.showerror("Notepad++", m),
                )
            else:
                self.set_status(f"Открыт собранный файл: {out_path}")

                if msg:
                    self.root.after(
                        0,
                        lambda m=msg: messagebox.showinfo("Notepad++", m),
                    )

        except Exception as e:
            self.log(traceback.format_exc())

            self.root.after(
                0,
                lambda m=str(e): messagebox.showerror(
                    "Ошибка сборки",
                    m + "\nПодробности во вкладке Диагностика.",
                ),
            )

        finally:
            if iid:
                self.root.after(0, lambda: self.set_busy(iid, False))

    def generate_assembled_file(self, root_path):
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)

        root_path = Path(root_path)

        safe_parent = "".join(
            c if c.isalnum() or c in ("_", "-") else "_"
            for c in root_path.parent.name
        ).lower()

        safe_stem = "".join(
            c if c.isalnum() or c in ("_", "-") else "_"
            for c in root_path.stem
        ).lower()

        out_path = GENERATED_DIR / f"{safe_parent}__{safe_stem}__assembled.ltx"

        text = self.generate_assembled_text(root_path)
        out_path.write_text(text, encoding="utf-8")

        return out_path

    def generate_assembled_text(self, root_path):
        mod = self.get_resolver_module()

        cls = getattr(mod, "LtxResolver", None)
        if cls is None:
            raise RuntimeError("В resolver4.py не найден класс LtxResolver")

        try:
            resolver = cls(str(root_path), use_cache=False, verbose=False)
        except TypeError:
            resolver = cls(str(root_path), verbose=False)

        self.log(f"resolver4 load: {root_path}")
        resolver.load()
        self.log(f"resolver4 готово для: {root_path}")

        source_map = self._get_source_map()

        lines = []
        lines.append(f"; Assembled root: {root_path}")
        lines.append(f"; Resolver module: {getattr(mod, '__file__', '')}")
        lines.append("")

        for sec_name, sect in resolver.data:
            sec_source = ""

            for it in sect.data:
                fn = getattr(it, "filename", "")
                if fn:
                    sec_source = self._pretty_source(fn, source_map)
                    break

            if sec_source:
                lines.append(f"[{sec_name}] ; <= {sec_source}")
            else:
                lines.append(f"[{sec_name}]")

            for it in sect.data:
                key = str(getattr(it, "first", "") or "")
                val = getattr(it, "second", "")

                if val is None:
                    val_s = ""
                elif hasattr(val, "name") and type(val).__name__ == "DLTXToken":
                    val_s = f"<{val.name}>"
                else:
                    val_s = str(val)

                src = self._pretty_source(getattr(it, "filename", ""), source_map)

                if "\n" in val_s or "\r" in val_s:
                    lines.append(f"{key} = {val_s}")
                    lines.append(f"; <= {src}")
                else:
                    if val_s == "":
                        lines.append(f"{key} = ; <= {src}")
                    else:
                        lines.append(f"{key} = {val_s} ; <= {src}")

            lines.append("")

        return "\n".join(lines)

    def _get_source_map(self):
        if self.source_map is not None:
            return self.source_map

        mp = {}

        if self.cache:
            for f in self.cache.get("files", []):
                rel = self.cache["rel_paths"].get(f, str(f))

                full_no_ext = os.path.splitext(str(f).lower())[0]
                mp[full_no_ext] = rel

                base_no_ext = os.path.splitext(f.name.lower())[0]

                if base_no_ext not in mp:
                    mp[base_no_ext] = rel

        self.source_map = mp
        return mp

    def _pretty_source(self, source, source_map):
        if not source:
            return "?"

        s = str(source).lower()

        if s in source_map:
            return source_map[s]

        s_no_ext = os.path.splitext(s)[0]

        if s_no_ext in source_map:
            return source_map[s_no_ext]

        base_no_ext = os.path.basename(s_no_ext)

        if base_no_ext in source_map:
            return source_map[base_no_ext]

        return source

    # ------------------------------------------------------------
    # Сортировка
    # ------------------------------------------------------------

    def on_sort(self, col):
        if not self.cache:
            return

        if self.sort_col == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_col = col
            self.sort_reverse = False

        self._update_headings()
        self.rebuild_tree()

    def _update_headings(self):
        arrow = "▲" if not self.sort_reverse else "▼"

        base = {
            "#0": "Имя",
            "files": "Файлов в дереве",
            "children": "Прямых детей",
        }

        for col, text in base.items():
            if self.sort_col == "path" and col == "#0":
                self.tree.heading(col, text=f"{text} {arrow}")
            elif self.sort_col == col:
                self.tree.heading(col, text=f"{text} {arrow}")
            else:
                self.tree.heading(col, text=text)

    def _node_sort_key(self, path):
        rel = self.cache["rel_paths"].get(path, str(path)).lower()

        if self.sort_col == "files":
            return (
                self.cache["subtree_counts"].get(path, 1),
                rel,
            )

        if self.sort_col == "children":
            return (
                len(self.cache["display_children"].get(path, [])),
                rel,
            )

        return (
            rel,
            self.cache["subtree_counts"].get(path, 1),
        )

    # ------------------------------------------------------------
    # Дерево
    # ------------------------------------------------------------

    def rebuild_tree(self):
        if not self.cache:
            return

        expanded = self._get_expanded_paths()
        selected = self._get_selected_path()

        self.tree.delete(*self.tree.get_children())
        self.iid_to_path.clear()
        self.path_to_iid.clear()
        self.populated_paths.clear()
        self.item_type.clear()
        self.busy_items.clear()
        self.busy_count.clear()

        # Виртуальный корень проводника
        self.computer_iid = self.tree.insert(
            "",
            "end",
            text="Этот компьютер",
            values=("", ""),
        )
        self._set_icon(self.computer_iid, "computer")
        self.tree.item(self.computer_iid, open=True)

        scan_dir = Path(self.cache["scan_dir"])
        drive_label = f"{scan_dir.name} (LTX VFS)"

        self.drive_iid = self.tree.insert(
            self.computer_iid,
            "end",
            text=drive_label,
            values=(self.cache["total_files"], len(self.cache["roots"])),
        )
        self._set_icon(self.drive_iid, "drive")
        self.tree.item(self.drive_iid, open=True)

        roots = sorted(
            self.cache["roots"],
            key=self._node_sort_key,
            reverse=self.sort_reverse,
        )

        for path in roots:
            self._insert_node(self.drive_iid, path, populate_direct=True)

        for path in expanded:
            self._reveal_path(path, select=False)

        if selected:
            self._reveal_path(selected, select=True)

        system_root_str = self.cache.get("system_root", "")
        if system_root_str:
            system_iid = self.path_to_iid.get(Path(system_root_str))
            if system_iid:
                self.tree.item(system_iid, open=True)

        self.set_status(
            f"Корней: {len(self.cache['roots'])}; "
            f"сортировка: {self.sort_col}, reverse={self.sort_reverse}"
        )

    def _get_expanded_paths(self):
        expanded = []

        def rec(parent):
            for iid in self.tree.get_children(parent):
                p = self.iid_to_path.get(iid)

                if p and self.tree.item(iid, "open"):
                    expanded.append(p)

                rec(iid)

        rec("")
        return expanded

    def _get_selected_path(self):
        iid = self.tree.focus()
        return self.iid_to_path.get(iid)

    def _insert_node(self, parent_iid, path, populate_direct=False):
        existing = self.path_to_iid.get(path)

        if existing and self.tree.exists(existing):
            return existing

        self.iid_counter += 1
        iid = f"n{self.iid_counter}"

        rel = self.cache["rel_paths"].get(path, str(path))
        files_count = self.cache["subtree_counts"].get(path, 1)
        children_count = len(self.cache["display_children"].get(path, []))

        self.tree.insert(
            parent_iid,
            "end",
            iid=iid,
            text=rel,
            values=(files_count, children_count),
        )

        self.iid_to_path[iid] = path
        self.path_to_iid[path] = iid

        children = self.cache["display_children"].get(path, [])

        if children:
            self._set_icon(iid, "folder")

            if populate_direct:
                sorted_children = sorted(
                    children,
                    key=self._node_sort_key,
                    reverse=self.sort_reverse,
                )

                for child_path in sorted_children:
                    self._insert_node(iid, child_path, populate_direct=False)

                self.populated_paths.add(path)
            else:
                ph_iid = f"ph{self.iid_counter}"
                self.tree.insert(
                    iid,
                    "end",
                    iid=ph_iid,
                    text="",
                    values=("", ""),
                )
        else:
            self._set_icon(iid, "file")

        return iid

    def _populate_item(self, iid):
        path = self.iid_to_path.get(iid)

        if not path:
            return

        if path in self.populated_paths:
            return

        children = self.cache["display_children"].get(path, [])

        if not children:
            return

        for ch in self.tree.get_children(iid):
            self.tree.delete(ch)

        sorted_children = sorted(
            children,
            key=self._node_sort_key,
            reverse=self.sort_reverse,
        )

        for child_path in sorted_children:
            self._insert_node(iid, child_path)

        self.populated_paths.add(path)

    def on_tree_open(self, event):
        iid = self.tree.focus()

        if not iid:
            return

        if iid in (self.computer_iid, self.drive_iid):
            return

        path = self.iid_to_path.get(iid)

        if path and self.cache["display_children"].get(path):
            self._set_icon(iid, "folder_open")

        self._populate_item(iid)

    def on_tree_close(self, event):
        iid = self.tree.focus()

        if not iid:
            return

        if iid in (self.computer_iid, self.drive_iid):
            return

        path = self.iid_to_path.get(iid)

        if path and self.cache["display_children"].get(path):
            self._set_icon(iid, "folder")

    def _reveal_path(self, path, select=True):
        if not self.cache:
            return

        chain = []
        cur = path

        while cur is not None:
            chain.append(cur)
            cur = self.cache["node_parent"].get(cur)

        chain.reverse()

        if self.computer_iid:
            self.tree.item(self.computer_iid, open=True)
        if self.drive_iid:
            self.tree.item(self.drive_iid, open=True)

        parent_iid = self.drive_iid
        target_iid = None

        for p in chain:
            iid = self.path_to_iid.get(p)

            if iid is None:
                iid = self._insert_node(parent_iid, p)

            if self.cache["display_children"].get(p):
                self._populate_item(iid)

            if p != path:
                self.tree.item(iid, open=True)

            parent_iid = iid
            target_iid = iid

        if select and target_iid:
            self.tree.selection_set(target_iid)
            self.tree.focus(target_iid)
            self.tree.see(target_iid)

    # ------------------------------------------------------------
    # Активация узлов
    # ------------------------------------------------------------

    def on_tree_activate(self, event=None):
        iid = self.tree.focus()
        path = self.iid_to_path.get(iid)

        if not path:
            return

        if path in self.cache["roots_set"]:
            self.open_assembled_in_notepad(path)
        else:
            messagebox.showinfo(
                "Просмотр детей",
                "Просмотр детей пока недоступен.",
            )

    # ------------------------------------------------------------
    # Поиск
    # ------------------------------------------------------------

    def on_search(self, event=None):
        if not self.cache:
            messagebox.showinfo("Нет данных", "Сначала нажмите Получить.")
            return

        text = self.search_var.get().strip().lower()

        if not text:
            return

        if text != self.last_search:
            matches = []

            for path in self.cache["files"]:
                fname = os.path.basename(str(path)).lower()
                rpath = self.cache["rel_paths"].get(path, "").lower()

                if text in fname or text in rpath:
                    matches.append(path)

            matches.sort(key=lambda p: self.cache["rel_paths"].get(p, str(p)).lower())

            self.search_matches = matches
            self.search_index = 0
            self.last_search = text
        else:
            if self.search_matches:
                self.search_index = (self.search_index + 1) % len(self.search_matches)

        if not self.search_matches:
            messagebox.showinfo(
                "Поиск",
                f"Не найдено файлов по запросу:\n{text}",
            )
            return

        path = self.search_matches[self.search_index]
        self._reveal_path(path, select=True)

        self.set_status(
            f"Найдено {self.search_index + 1}/{len(self.search_matches)}: "
            f"{self.cache['rel_paths'].get(path, str(path))}"
        )

    # ------------------------------------------------------------
    # Сохранение / копирование
    # ------------------------------------------------------------

    def generate_report(self):
        if not self.cache:
            return "Нет данных. Нажмите Получить."

        lines = []

        lines.append(f"Папка сканирования: {self.cache['scan_dir']}")
        lines.append(f"System.ltx: {self.cache['system_root']}")
        lines.append(f"Секций в resolver: {self.cache.get('resolver_sections', 'N/A')}")
        lines.append(f"Всего .ltx файлов: {self.cache.get('total_files', 0)}")
        lines.append(f"Файлов в дереве System.ltx: {self.cache.get('system_tree_files', 0)}")
        lines.append(f"Корней верхнего уровня: {len(self.cache['roots'])}")
        lines.append("")
        lines.append("root;files_in_tree;direct_children")

        roots = sorted(
            self.cache["roots"],
            key=self._node_sort_key,
            reverse=self.sort_reverse,
        )

        for path in roots:
            rel = self.cache["rel_paths"].get(path, str(path))
            files_count = self.cache["subtree_counts"].get(path, 1)
            children_count = len(self.cache["display_children"].get(path, []))

            lines.append(f"{rel};{files_count};{children_count}")

        return "\n".join(lines)

    def save(self):
        if not self.cache:
            messagebox.showinfo("Нет данных", "Сначала нажмите Получить.")
            return

        f = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
            initialfile="ltx_tree.txt",
        )

        if not f:
            return

        try:
            Path(f).write_text(self.generate_report(), encoding="utf-8")
            self.set_status(f"Сохранено: {f}")
        except Exception as e:
            messagebox.showerror("Ошибка сохранения", str(e))

    def copy(self):
        if not self.cache:
            messagebox.showinfo("Нет данных", "Сначала нажмите Получить.")
            return

        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.generate_report())
            self.set_status("Скопировано в буфер обмена.")
        except Exception as e:
            messagebox.showerror("Ошибка копирования", str(e))

    def run(self):
        self.root.mainloop()


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
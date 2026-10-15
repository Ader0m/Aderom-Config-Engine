# -*- coding: utf-8 -*-
"""theme.py — визуальные ресурсы: токены, ttk-тема, PNG-иконки и спиннер.
Не импортирует tkinter и не знает о логике леса."""

import math
import zlib
import struct
import base64

SPIN_FRAMES = 12

TOKENS = {
    "VS2022": {
        "BG_APP": "#1E1E1E", "BG_SIDE": "#252526", "BG_PANEL": "#2D2D30",
        "BG_CTRL": "#333333", "BG_HOVER": "#3E3E42", "BG_SELECT": "#094771",
        "BG_SELECT_IN": "#37373D", "BORDER": "#3F3F46",
        "TXT_PRIMARY": "#D4D4D4", "TXT_SECONDARY": "#9D9D9D",
        "TXT_DISABLED": "#6A6A6A",
        "ACCENT": "#007ACC", "ACCENT_HOVER": "#1177DD", "INFO": "#4FC1FF",
        "OK": "#4EC9B0", "WARN": "#CCA700", "ERR": "#F44747",
        "FONT_UI": ("Segoe UI", 9), "FONT_UI_BOLD": ("Segoe UI", 9, "bold"),
        "FONT_MONO": ("Cascadia Code", 10), "ROW_H": 24,
    },
    "Qwen": {
        "BG_APP": "#171717", "BG_SIDE": "#1F1F1F", "BG_PANEL": "#262626",
        "BG_CTRL": "#303030", "BG_HOVER": "#3A3A3A", "BG_SELECT": "#3B3B63",
        "BG_SELECT_IN": "#333340", "BORDER": "#3A3A3A",
        "TXT_PRIMARY": "#D6D6D6", "TXT_SECONDARY": "#9A9A9A",
        "TXT_DISABLED": "#666666",
        "ACCENT": "#615CED", "ACCENT_HOVER": "#7A70F0", "INFO": "#8B85FF",
        "OK": "#4EC9B0", "WARN": "#CCA700", "ERR": "#F44747",
        "FONT_UI": ("Segoe UI", 9), "FONT_UI_BOLD": ("Segoe UI", 9, "bold"),
        "FONT_MONO": ("Cascadia Code", 10), "ROW_H": 24,
    },
}


def apply_theme(style, variant="VS2022") -> str:
    t = TOKENS[variant]
    name = "main5_dark"
    if name not in style.theme_names():
        style.theme_create(name, parent="clam")
    style.theme_use(name)

    style.configure(".", background=t["BG_APP"], foreground=t["TXT_PRIMARY"],
                    borderwidth=0, focusthickness=0, font=t["FONT_UI"])
    style.configure("TFrame", background=t["BG_APP"])
    style.configure("Side.TFrame", background=t["BG_SIDE"])
    style.configure("Panel.TFrame", background=t["BG_PANEL"])
    style.configure("TLabel", background=t["BG_APP"], foreground=t["TXT_PRIMARY"])
    style.configure("Side.TLabel", background=t["BG_SIDE"],
                    foreground=t["TXT_SECONDARY"])
    style.configure("Status.TLabel", background=t["BG_PANEL"],
                    foreground=t["TXT_SECONDARY"], padding=(6, 2))
    style.configure("StatusMode.TLabel", background=t["ACCENT"],
                    foreground="#FFFFFF", padding=(8, 2))

    style.configure("TButton", background=t["BG_CTRL"],
                    foreground=t["TXT_PRIMARY"], borderwidth=1,
                    focusthickness=0, padding=(10, 4))
    style.map("TButton",
              background=[("pressed", t["ACCENT"]), ("active", t["BG_HOVER"]),
                          ("disabled", t["BG_CTRL"])],
              foreground=[("disabled", t["TXT_DISABLED"])])
    style.configure("Primary.TButton", background=t["ACCENT"],
                    foreground="#FFFFFF")
    style.map("Primary.TButton",
              background=[("pressed", t["ACCENT_HOVER"]),
                          ("active", t["ACCENT_HOVER"]),
                          ("disabled", t["BG_CTRL"])],
              foreground=[("disabled", t["TXT_DISABLED"])])

    style.configure("TEntry", fieldbackground=t["BG_CTRL"],
                    foreground=t["TXT_PRIMARY"], insertcolor=t["TXT_PRIMARY"],
                    borderwidth=1, padding=3)
    style.map("TEntry", bordercolor=[("focus", t["ACCENT"])])

    style.configure("Treeview", background=t["BG_SIDE"],
                    fieldbackground=t["BG_SIDE"], foreground=t["TXT_PRIMARY"],
                    rowheight=t["ROW_H"], borderwidth=0)
    style.map("Treeview",
              background=[("selected", t["BG_SELECT"])],
              foreground=[("selected", t["TXT_PRIMARY"])])
    style.configure("Treeview.Heading", background=t["BG_PANEL"],
                    foreground=t["TXT_SECONDARY"], borderwidth=0,
                    padding=(6, 4))
    style.map("Treeview.Heading", background=[("active", t["BG_HOVER"])])

    style.configure("TNotebook", background=t["BG_APP"], borderwidth=0)
    style.configure("TNotebook.Tab", background=t["BG_PANEL"],
                    foreground=t["TXT_SECONDARY"], padding=(12, 5),
                    borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", t["BG_SIDE"])],
              foreground=[("selected", t["TXT_PRIMARY"])])

    for orient in ("Vertical", "Horizontal"):
        style.configure("%s.TScrollbar" % orient, background=t["BG_PANEL"],
                        troughcolor=t["BG_APP"], borderwidth=0,
                        arrowcolor=t["TXT_SECONDARY"])
        style.map("%s.TScrollbar" % orient,
                  background=[("active", t["BG_HOVER"])])
    try:
        style.configure("TPanedwindow", background=t["BORDER"])
        style.configure("Sash", gripcolor=t["BORDER"], sashthickness=4)
    except Exception:
        pass
    return name


def apply_dark_titlebar(hwnd) -> bool:
    try:
        import ctypes
        val = ctypes.c_int(1)
        return 0 == ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(val), 4)
    except Exception:
        return False


# ---------------------------------------------------------------- PNG/иконки
def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _png(width, height, rows) -> str:
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(raw, 6))
    png += _chunk(b"IEND", b"")
    return base64.b64encode(png).decode("ascii")


class PixBuf:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.rows = [bytearray(w * 4) for _ in range(h)]

    def put(self, x, y, r, g, b, a=255):
        if 0 <= x < self.w and 0 <= y < self.h:
            row = self.rows[y]
            o = x * 4
            row[o], row[o + 1], row[o + 2], row[o + 3] = r, g, b, a


def _blit(buf, x0, y0, rows, pal):
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            c = pal.get(ch)
            if c:
                buf.put(x0 + x, y0 + y, *_hex(c))


def _spinner(buf, x0, y0, k):
    cx, cy = x0 + 7.5, y0 + 7.5
    start = k * 2 * math.pi / SPIN_FRAMES
    for y in range(y0, y0 + 16):
        for x in range(x0, x0 + 16):
            r = math.hypot(x - cx, y - cy)
            if 4.6 <= r <= 7.2:
                diff = (math.atan2(y - cy, x - cx) - start) % (2 * math.pi)
                if diff < math.pi * 0.5:
                    col = (40, 120, 220)
                elif diff < math.pi * 0.8:
                    col = (120, 170, 230)
                else:
                    col = (200, 210, 220)
                buf.put(x, y, *col)


_D = "."
ICON_DEFS = {
    "computer": ([_D * 16, "..DDDDDDDDDDDD..", "..DBBBBBBBBBBD.."] * 1
                 and [_D * 16, "..DDDDDDDDDDDD..", "..DBBBBBBBBBBD..",
                      "..DBBBBBBBBBBD..", "..DBBBBBBBBBBD..", "..DBBBBBBBBBBD..",
                      "..DBBBBBBBBBBD..", "..DBBBBBBBBBBD..", "..DDDDDDDDDDDD..",
                      ".......DD.......", ".......DD.......", "....DDDDDDDD....",
                      "....DDDDDDDD....", _D * 16, _D * 16, _D * 16],
                 {"D": "#4a5568", "B": "#7fb2e5"}),
    "drive": ([_D * 16] * 3 + [".DDDDDDDDDDDDDD.", ".DGGGGGGGGGGGGD.",
              ".DGGGGGGGGGGGYD.", ".DGGGGGGGGGGGGD.", ".DDDDDDDDDDDDDD."]
              + [_D * 16] * 8,
              {"D": "#4a5568", "G": "#cfd6dd", "Y": "#e5b53a"}),
    "folder": ([_D * 16, _D * 16, ".DDDDDD" + _D * 9, ".DLLLLDDDDDDDDD.",
               ".DLLLLLLLLLLLLD."] + [".DYYYYYYYYYYYYD."] * 7
               + [".DDDDDDDDDDDDDD."] + [_D * 16] * 3,
               {"D": "#8a6a1f", "L": "#f6e3a1", "Y": "#e9c463"}),
    "folder_open": ([_D * 16, _D * 16, ".DDDDDD" + _D * 9, ".DLLLLDDDDDDDDD.",
                    ".DLLLLLLLLLLLLD.", ".DYYYYYYYYYYYYD.", ".DYYYYYYYYYYYYD.",
                    ".DDDDDDDDDDDDDD."] + [".DLLLLLLLLLLLLD."] * 4
                    + [".DDDDDDDDDDDDDD."] + [_D * 16] * 3,
                    {"D": "#8a6a1f", "L": "#f6e3a1", "Y": "#e9c463"}),
    "file": ([_D * 16, _D * 16, "...DDDDDDDDDD...", "...DWWWWWWWWD..."]
             + ["...DWGWGWGWWD..." if i % 2 else "...DWWWWWWWWD..."
                for i in range(8)]
             + ["...DDDDDDDDDD...", _D * 16, _D * 16, _D * 16],
             {"D": "#5a6470", "W": "#ffffff", "G": "#9aa5b1"}),
}


def build_icon_pngs() -> dict:
    out = {}
    for name, (rows, pal) in ICON_DEFS.items():
        idle = PixBuf(32, 16)
        _blit(idle, 0, 0, rows, pal)
        busy = []
        for k in range(SPIN_FRAMES):
            b = PixBuf(32, 16)
            _blit(b, 0, 0, rows, pal)
            _spinner(b, 16, 0, k)
            busy.append(_png(b.w, b.h, b.rows))
        out[name] = {"idle": _png(idle.w, idle.h, idle.rows), "busy": busy}
    return out
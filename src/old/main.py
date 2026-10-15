# -*- coding: utf-8 -*-
"""Точка входа."""
import ctypes


def _dpi():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


if __name__ == "__main__":
    _dpi()
    from ui import App
    App().run()
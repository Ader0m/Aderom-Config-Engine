# -*- coding: utf-8 -*-
"""ui.py — визуальный слой: проводник LTX, спиннер, сортировка, поиск."""

import os
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext

import core
import theme

SCRIPT_DIR = Path(__file__).resolve().parent
GENERATED_DIR = SCRIPT_DIR / "generated"
DEFAULT_CONFIG_DIR = r"H:\GRA2.5\Anomaly 1.5.3 up\gamedata\configs"
TICK_MS = 80


class App:
    def __init__(self, variant="VS2022"):
        self.t = theme.TOKENS[variant]
        self.root = tk.Tk()
        self.root.title("Main5 — LTX Explorer")
        self.root.geometry("1380x900")

        style = ttk.Style(self.root)
        theme.apply_theme(style, variant)

        pngs = theme.build_icon_pngs()
        self._img_refs = []
        self.idle_img, self.busy_img = {}, {}
        for name, d in pngs.items():
            img = tk.PhotoImage(data=d["idle"])
            self._img_refs.append(img)
            self.idle_img[name] = img
            self.busy_img[name] = []
            for fr in d["busy"]:
                im = tk.PhotoImage(data=fr)
                self._img_refs.append(im)
                self.busy_img[name].append(im)

        self.forest = None
        self.assembler = None
        self.config_dir = Path(DEFAULT_CONFIG_DIR)
        if not self.config_dir.is_dir():
            self.config_dir = None

        self.sort_col, self.sort_reverse = "path", False
        self.iid_counter = 0
        self.iid_to_path, self.path_to_iid = {}, {}
        self.populated, self.item_type = set(), {}
        self.computer_iid = self.drive_iid = None
        self.spin_frame = 0
        self.busy_items, self.busy_count = {}, {}
        self.search_matches, self.search_index, self.last_search = [], 0, ""

        self.status_mode = tk.StringVar(value="Готов")
        self.status_mid = tk.StringVar(value="")
        self.status_right = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")

        self._build_ui()
        self._dark_titlebar()
        self.root.after(TICK_MS, self._tick)

    # ------------------------------------------------------------ ui build
    def _dark_titlebar(self):
        try:
            import ctypes
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            theme.apply_dark_titlebar(hwnd)
        except Exception:
            pass

    def _build_ui(self):
        t = self.t
        main = ttk.Frame(self.root, padding=5)
        main.pack(fill="both", expand=True)

        bar = ttk.Frame(main, style="Panel.TFrame")
        bar.pack(side="bottom", fill="x", pady=(4, 0))
        ttk.Label(bar, style="StatusMode.TLabel",
                  textvariable=self.status_mode).pack(side="left")
        ttk.Label(bar, style="Status.TLabel",
                  textvariable=self.status_mid).pack(side="left", fill="x",
                                                      expand=True)
        ttk.Label(bar, style="Status.TLabel",
                  textvariable=self.status_right).pack(side="right")

        top = ttk.Frame(main, style="Panel.TFrame")
        top.pack(fill="x", pady=(0, 4))
        ttk.Button(top, text="Выбрать папку", command=self.choose_dir,
                   width=14).pack(side="left", padx=2)
        ttk.Label(top, style="Side.TLabel",
                  text=str(self.config_dir or "<папка не выбрана>"),
                  anchor="w").pack(side="left", fill="x", expand=True, padx=4)

        btns = ttk.Frame(main)
        btns.pack(fill="x", pady=(0, 4))
        self.get_btn = ttk.Button(btns, text="Получить", style="Primary.TButton",
                                  command=self.on_get)
        self.get_btn.pack(side="left", padx=(0, 4))
        ttk.Button(btns, text="Сохранить", command=self.save).pack(side="left",
                                                                   padx=(0, 4))
        ttk.Button(btns, text="Копировать", command=self.copy).pack(side="left",
                                                                    padx=(0, 12))
        ttk.Label(btns, text="Поиск файла:").pack(side="left", padx=(0, 4))
        self.search_entry = ttk.Entry(btns, textvariable=self.search_var,
                                      width=45)
        self.search_entry.pack(side="left", padx=(0, 4))
        self.search_entry.bind("<Return>", self.on_search)
        ttk.Button(btns, text="Найти", command=self.on_search).pack(side="left")

        body = ttk.Frame(main, style="Side.TFrame")
        body.pack(fill="both", expand=True)

        hdr = ttk.Label(body, style="Side.TLabel", text="ПРОВОДНИК LTX",
                        padding=(8, 4))
        hdr.pack(fill="x")

        tf = ttk.Frame(body, style="Side.TFrame")
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=("files", "children"),
                                 show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Имя",
                          command=lambda: self.on_sort("path"))
        self.tree.heading("files", text="Файлов в дереве",
                          command=lambda: self.on_sort("files"))
        self.tree.heading("children", text="Прямых детей",
                          command=lambda: self.on_sort("children"))
        self.tree.column("#0", width=880, anchor="w")
        self.tree.column("files", width=150, anchor="center", stretch=False)
        self.tree.column("children", width=150, anchor="center", stretch=False)
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=ys.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewOpen>>", self.on_tree_open)
        self.tree.bind("<<TreeviewClose>>", self.on_tree_close)
        self.tree.bind("<Double-1>", self.on_activate)
        self.tree.bind("<Return>", self.on_activate)
        self.tree.bind("<Button-3>", self._popup)

        self.menu = tk.Menu(self.tree, tearoff=0, bg=t["BG_CTRL"],
                            fg=t["TXT_PRIMARY"], activebackground=t["BG_SELECT"],
                            activeforeground=t["TXT_PRIMARY"], relief="flat")
        self.menu.add_command(label="Собрать и открыть в Notepad++",
                              command=self.ctx_assemble)
        self.menu.add_command(label="Копировать относительный путь",
                              command=self.ctx_copy)

        nb = ttk.Notebook(main)
        nb.pack(fill="both", expand=False)
        tab = ttk.Frame(nb)
        nb.add(tab, text="ДИАГНОСТИКА")
        self.txt = scrolledtext.ScrolledText(tab, wrap="none", undo=False,
                                             bg=t["BG_SIDE"],
                                             fg=t["TXT_PRIMARY"],
                                             insertbackground=t["TXT_PRIMARY"],
                                             font=t["FONT_MONO"],
                                             relief="flat", borderwidth=0)
        self.txt.pack(fill="both", expand=True)
        self.txt.configure(state="disabled", height=10)
        self._update_headings()

    # ------------------------------------------------------------ сервис
    def log(self, msg):
        self.root.after(0, self._log_gui, str(msg))

    def _log_gui(self, msg):
        self.txt.configure(state="normal")
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def set_mode(self, msg):
        self.root.after(0, lambda: self.status_mode.set(str(msg)))

    def show_dialog(self, title, msg, error=False):
        t = self.t
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=t["BG_SIDE"])
        win.resizable(False, False)
        win.transient(self.root)
        fr = tk.Frame(win, bg=t["BG_SIDE"], padx=16, pady=12)
        fr.pack(fill="both", expand=True)
        tk.Label(fr, text=msg, bg=t["BG_SIDE"],
                 fg=t["ERR"] if error else t["TXT_PRIMARY"],
                 justify="left", font=t["FONT_UI"]).pack(anchor="w")
        tk.Button(fr, text="OK", bg=t["BG_CTRL"], fg=t["TXT_PRIMARY"],
                  activebackground=t["BG_HOVER"], relief="flat", padx=14,
                  pady=4, command=win.destroy).pack(pady=(10, 0))
        win.grab_set()

    # ------------------------------------------------------------ спиннер
    def _tick(self):
        self.spin_frame = (self.spin_frame + 1) % theme.SPIN_FRAMES
        for iid, typ in list(self.busy_items.items()):
            try:
                if self.tree.exists(iid):
                    self.tree.item(iid, image=self.busy_img[typ][self.spin_frame])
            except tk.TclError:
                pass
        self.root.after(TICK_MS, self._tick)

    def _set_icon(self, iid, typ):
        self.item_type[iid] = typ
        if iid in self.busy_items:
            self.busy_items[iid] = typ
            img = self.busy_img[typ][self.spin_frame]
        else:
            img = self.idle_img[typ]
        self.tree.item(iid, image=img)

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

    def run_task(self, iid, fn, *args, on_done=None):
        if iid:
            self.set_busy(iid, True)

        def worker():
            res, err = None, None
            try:
                res = fn(*args)
            except Exception as e:
                err = e
                self.log(traceback.format_exc())
            finally:
                self.root.after(0, lambda: self._done(iid, res, err, on_done))
        threading.Thread(target=worker, daemon=True).start()

    def _done(self, iid, res, err, on_done):
        if iid:
            self.set_busy(iid, False)
        if on_done:
            on_done(res, err)

    # ------------------------------------------------------------ задачи
    def choose_dir(self):
        d = filedialog.askdirectory(initialdir=str(self.config_dir or "."))
        if d:
            self.config_dir = Path(d)
            self.forest = None

    def on_get(self):
        if not self.config_dir or not self.config_dir.is_dir():
            self.choose_dir()
            if not self.config_dir or not self.config_dir.is_dir():
                return
        self.get_btn.config(state="disabled")
        self.set_mode("Сканирование…")
        self.run_task(None, self._scan)

    def _scan(self):
        sel = self.config_dir
        system = core.find_system_ltx(sel)
        if system is None:
            raise RuntimeError("system.ltx не найден в %s" % sel)
        scan_dir = sel if (sel / "system.ltx").is_file() else system.parent
        self.log("Сканирую: %s" % scan_dir)
        forest = core.build_forest(scan_dir, system, log=self.log)
        self.forest = forest
        self.root.after(0, self.rebuild_tree)
        self.root.after(0, self._start_system_pass)
        return forest

    def _ensure_assembler(self):
        if self.assembler is None:
            bases = core.build_search_bases(self.forest.scan_dir,
                                            self.config_dir)
            script = core.find_resolver_script(bases)
            if script is None:
                raise FileNotFoundError("resolver4.py не найден")
            self.assembler = core.Assembler(script)
        return self.assembler

    def _start_system_pass(self):
        self.get_btn.config(state="normal")
        f = self.forest
        self.status_mid.set("Корней: %d; файлов: %d; в дереве system: %d"
                            % (len(f.roots), f.total_files, f.system_tree_files))
        if not f.system_root:
            return
        iid = self.path_to_iid.get(Path(f.system_root))
        if not iid:
            return
        self.set_mode("Сборка: system.ltx…")

        def pass_sys():
            a = self._ensure_assembler()
            return a.pass_only(f.system_root)

        def done(res, err):
            if err is None:
                f.resolver_sections = res
                self.log("resolver4 pass: секций %s" % res)
                self.set_mode("Готов")
            else:
                self.set_mode("Ошибка")
        self.run_task(iid, pass_sys, on_done=done)

    def _assemble_and_open(self, path):
        a = self._ensure_assembler()
        sm = core.build_source_map(self.forest)
        out = a.assemble_to_file(path, GENERATED_DIR, sm)
        ok, msg = core.open_in_notepad_pp(out)
        if not ok:
            raise RuntimeError(msg)
        return out

    # ------------------------------------------------------------ дерево
    def rebuild_tree(self):
        f = self.forest
        if not f:
            return
        self.tree.delete(*self.tree.get_children())
        self.iid_to_path.clear(); self.path_to_iid.clear()
        self.populated.clear(); self.item_type.clear()
        self.busy_items.clear(); self.busy_count.clear()

        self.computer_iid = self.tree.insert("", "end", text="Этот компьютер",
                                             values=("", ""))
        self._set_icon(self.computer_iid, "computer")
        self.tree.item(self.computer_iid, open=True)

        sd = Path(f.scan_dir)
        self.drive_iid = self.tree.insert(self.computer_iid, "end",
                                          text="%s (LTX VFS)" % sd.name,
                                          values=(f.total_files,
                                                  len(f.roots)))
        self._set_icon(self.drive_iid, "drive")
        self.tree.item(self.drive_iid, open=True)

        for p in core.sort_nodes(f, f.roots, self.sort_col, self.sort_reverse):
            self._insert_node(self.drive_iid, p, populate_direct=True)

        if f.system_root:
            iid = self.path_to_iid.get(Path(f.system_root))
            if iid:
                self.tree.item(iid, open=True)

    def _insert_node(self, parent_iid, path, populate_direct=False):
        ex = self.path_to_iid.get(path)
        if ex and self.tree.exists(ex):
            return ex
        f = self.forest
        self.iid_counter += 1
        iid = "n%d" % self.iid_counter
        kids = f.display_children.get(path, [])
        self.tree.insert(parent_iid, "end", iid=iid,
                         text=f.rel_paths.get(path, str(path)),
                         values=(f.subtree_counts.get(path, 1), len(kids)))
        self.iid_to_path[iid] = path
        self.path_to_iid[path] = iid
        if kids:
            self._set_icon(iid, "folder")
            if populate_direct:
                for c in core.sort_nodes(f, kids, self.sort_col,
                                         self.sort_reverse):
                    self._insert_node(iid, c, populate_direct=False)
                self.populated.add(path)
            else:
                self.tree.insert(iid, "end", iid="ph%d" % self.iid_counter,
                                 text="", values=("", ""))
        else:
            self._set_icon(iid, "file")
        return iid

    def _populate_item(self, iid):
        path = self.iid_to_path.get(iid)
        if not path or path in self.populated:
            return
        kids = self.forest.display_children.get(path, [])
        if not kids:
            return
        for ch in self.tree.get_children(iid):
            self.tree.delete(ch)
        for c in core.sort_nodes(self.forest, kids, self.sort_col,
                                 self.sort_reverse):
            self._insert_node(iid, c)
        self.populated.add(path)

    def on_tree_open(self, event):
        iid = self.tree.focus()
        if iid in (self.computer_iid, self.drive_iid) or not iid:
            return
        p = self.iid_to_path.get(iid)
        if p and self.forest.display_children.get(p):
            self._set_icon(iid, "folder_open")
        self._populate_item(iid)

    def on_tree_close(self, event):
        iid = self.tree.focus()
        p = self.iid_to_path.get(iid)
        if p and self.forest.display_children.get(p):
            self._set_icon(iid, "folder")

    def _reveal(self, path, select=True):
        f = self.forest
        chain = []
        cur = path
        while cur is not None:
            chain.append(cur)
            cur = f.node_parent.get(cur)
        chain.reverse()
        for v in (self.computer_iid, self.drive_iid):
            if v:
                self.tree.item(v, open=True)
        parent, target = self.drive_iid, None
        for p in chain:
            iid = self.path_to_iid.get(p) or self._insert_node(parent, p)
            if f.display_children.get(p):
                self._populate_item(iid)
            if p != path:
                self.tree.item(iid, open=True)
            parent, target = iid, iid
        if select and target:
            self.tree.selection_set(target)
            self.tree.focus(target)
            self.tree.see(target)

    # ------------------------------------------------------------ действия
    def on_activate(self, event=None):
        path = self.iid_to_path.get(self.tree.focus())
        if not path:
            return
        if path in self.forest.roots_set:
            self._open_assembled(path)
        else:
            self.show_dialog("Просмотр детей",
                             "Просмотр детей пока недоступен.")

    def _open_assembled(self, path):
        iid = self.path_to_iid.get(path)
        self.set_mode("Сборка: %s…" % path.name)

        def done(res, err):
            if err is None:
                self.set_mode("Открыт: %s" % res.name)
            else:
                self.set_mode("Ошибка")
                self.show_dialog("Ошибка сборки", str(err), error=True)
        self.run_task(iid, self._assemble_and_open, path, on_done=done)

    def _popup(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            try:
                self.menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.menu.grab_release()

    def ctx_assemble(self):
        path = self.iid_to_path.get(self.tree.focus())
        if path and path in self.forest.roots_set:
            self._open_assembled(path)

    def ctx_copy(self):
        path = self.iid_to_path.get(self.tree.focus())
        if path:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.forest.rel_paths.get(path,
                                                                   str(path)))

    # ------------------------------------------------------------ сортировка/поиск
    def on_sort(self, col):
        if not self.forest:
            return
        if self.sort_col == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_col, self.sort_reverse = col, False
        self._update_headings()
        self.rebuild_tree()

    def _update_headings(self):
        arrow = "▲" if not self.sort_reverse else "▼"
        base = {"#0": "Имя", "files": "Файлов в дереве",
                "children": "Прямых детей"}
        for col, text in base.items():
            key = "path" if col == "#0" else col
            self.tree.heading(col, text=text + (" " + arrow
                                                if self.sort_col == key
                                                else ""))

    def on_search(self, event=None):
        if not self.forest:
            return
        text = self.search_var.get().strip().lower()
        if not text:
            return
        if text != self.last_search:
            self.search_matches = core.search_nodes(self.forest, text)
            self.search_index = 0
            self.last_search = text
        elif self.search_matches:
            self.search_index = (self.search_index + 1) % len(self.search_matches)
        if not self.search_matches:
            self.show_dialog("Поиск", "Не найдено: %s" % text)
            return
        p = self.search_matches[self.search_index]
        self._reveal(p, select=True)
        self.status_right.set("Найдено %d/%d"
                              % (self.search_index + 1,
                                 len(self.search_matches)))

    # ------------------------------------------------------------ отчёты
    def _report(self):
        if not self.forest:
            return "Нет данных."
        return core.generate_report(self.forest, self.sort_col,
                                    self.sort_reverse)

    def save(self):
        if not self.forest:
            return
        f = filedialog.asksaveasfilename(defaultextension=".txt",
                                         initialfile="ltx_tree.txt")
        if f:
            Path(f).write_text(self._report(), encoding="utf-8")

    def copy(self):
        if self.forest:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._report())

    def run(self):
        self.root.mainloop()
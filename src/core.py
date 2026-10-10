# -*- coding: utf-8 -*-
"""core.py — доменный слой проекта LTX Explorer.
Не импортирует tkinter. Все функции синхронные."""

import os
import re
import sys
import shutil
import subprocess
import importlib.util
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

RESOLVER_CANDIDATES = ("Resolver4.py", "resolver4.py")


# ---------------------------------------------------------------- пути/маски
def normalize_path(p) -> Path:
    return Path(os.path.normpath(str(p))).resolve()


def rel_path(p, base) -> str:
    try:
        return os.path.relpath(str(p), str(base))
    except ValueError:
        return str(p)


def pattern_match(name: str, mask: str) -> bool:
    """FS-маска '*'/'?' (PatternMatch, LocatorAPI.cpp), регистронезависимо."""
    rx = "^" + "".join(
        ".*" if c == "*" else "." if c == "?" else re.escape(c)
        for c in str(mask).lower()
    ) + "$"
    return re.match(rx, str(name).lower()) is not None


def read_text_safe(path) -> str:
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("windows-1251", errors="replace")


def strip_comments(line: str) -> str:
    in_q = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == '"':
            in_q = not in_q
        elif not in_q:
            if ch == ";":
                return line[:i].strip()
            if ch == "/" and i + 1 < n and line[i + 1] == "/":
                return line[:i].strip()
        i += 1
    return line.strip()


INCLUDE_QUOTED_RE = re.compile(r'#include\s+"([^"]+)"', re.IGNORECASE)
INCLUDE_BARE_RE = re.compile(r'#include\s+([^\s;]+)', re.IGNORECASE)


def extract_includes(text: str):
    out = []
    for line in text.splitlines():
        s = strip_comments(line)
        if not s or not (s.startswith("#") and "include" in s.lower()):
            continue
        m = INCLUDE_QUOTED_RE.search(s)
        if m:
            out.append(m.group(1))
        else:
            m = INCLUDE_BARE_RE.search(s)
            if m:
                out.append(m.group(1).strip('"'))
    return out


def resolve_include_targets(inc_name: str, parent_path):
    """Разрешение #include относительно файла-родителя, с масками."""
    inc = str(inc_name or "").strip().strip('"').replace("\\", "/")
    if not inc:
        return []
    parent_dir = Path(parent_path).parent
    p = Path(inc)

    if p.is_absolute():
        if "*" in inc or "?" in inc:
            d, mask = p.parent, p.name
            if not d.is_dir():
                return []
            return sorted(
                (normalize_path(e) for e in d.iterdir()
                 if e.is_file() and e.suffix.lower() == ".ltx"
                 and pattern_match(e.name, mask)),
                key=lambda x: str(x).lower())
        f = normalize_path(p)
        return [f] if f.is_file() else []

    inc_rel = inc.lstrip("/")
    if "*" in inc_rel or "?" in inc_rel:
        parts = inc_rel.split("/")
        mask, subdir = parts[-1], "/".join(parts[:-1])
        d = parent_dir / subdir if subdir else parent_dir
        if not d.is_dir():
            return []
        return sorted(
            (normalize_path(e) for e in d.iterdir()
             if e.is_file() and e.suffix.lower() == ".ltx"
             and pattern_match(e.name, mask)),
            key=lambda x: str(x).lower())

    f = normalize_path(parent_dir / inc_rel)
    if f.is_file():
        return [f]
    f2 = normalize_path(parent_dir / inc)
    return [f2] if f2.is_file() else []


def scan_ltx_files(root_dir):
    out = set()
    try:
        for p in Path(root_dir).rglob("*"):
            try:
                if p.is_file() and p.suffix.lower() == ".ltx":
                    out.add(normalize_path(p))
            except OSError:
                pass
    except Exception:
        pass
    return sorted(out, key=lambda x: str(x).lower())


def find_system_ltx(selected_dir) -> Optional[Path]:
    sel = Path(selected_dir)
    for c in (sel / "system.ltx",
              sel / "gamedata" / "configs" / "system.ltx",
              sel / "configs" / "system.ltx"):
        if c.is_file():
            return normalize_path(c)
    for p in sel.rglob("system.ltx"):
        if p.is_file():
            return normalize_path(p)
    return None


def build_search_bases(scan_dir, selected_dir):
    bases = []
    for d in (scan_dir, selected_dir, Path(__file__).resolve().parent, Path.cwd()):
        if not d:
            continue
        p = Path(d)
        if p not in bases:
            bases.append(p)
        if p.parent not in bases:
            bases.append(p.parent)
    return bases


def find_resolver_script(bases) -> Optional[Path]:
    for base in bases:
        for name in RESOLVER_CANDIDATES:
            p = Path(base) / name
            if p.is_file():
                return normalize_path(p)
    for name in RESOLVER_CANDIDATES:
        try:
            spec = importlib.util.find_spec(Path(name).stem)
            if spec and spec.origin and Path(spec.origin).is_file():
                return normalize_path(spec.origin)
        except Exception:
            pass
    return None


def load_resolver_module(path):
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError("Resolver не найден: %s" % path)
    spec = importlib.util.spec_from_file_location(p.stem, str(p))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[p.stem] = mod
    spec.loader.exec_module(mod)
    return mod


def get_dltx_mods_for_root(root_path, folder_files):
    stem = Path(root_path).stem.lower()
    ambiguous = {f.stem.lower() for f in folder_files
                 if f.stem.lower().startswith(stem + "_")}
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
            if (name.startswith(head) and len(name) > len(head) + 4
                    and pattern_match(name, "mod_" + amb + "_*.ltx")):
                skip = True
                break
        if not skip:
            mods.append(f)
    return sorted(mods, key=lambda x: x.name.lower())


def open_in_notepad_pp(path):
    cands = []
    exe = shutil.which("notepad++")
    if exe:
        cands.append(exe)
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            cands.append(os.path.join(base, "Notepad++", "notepad++.exe"))
    for c in cands:
        try:
            if c and Path(c).is_file():
                subprocess.Popen([str(c), str(path)])
                return True, ""
        except Exception:
            pass
    return False, ("Notepad++ не найден (PATH, ProgramFiles, LocalAppData "
                   "проверены).")


# ---------------------------------------------------------------- лес
@dataclass
class LtxForest:
    scan_dir: str
    system_root: str
    total_files: int
    files: list
    rel_paths: dict
    roots: list
    roots_set: set
    display_children: dict
    node_parent: dict
    subtree_counts: dict
    system_tree_files: int
    resolver_sections: object = None


def build_forest(scan_dir, system_root, log=None) -> LtxForest:
    log = log or (lambda m: None)
    files = scan_ltx_files(scan_dir)
    file_set = set(files)
    rel_paths = {f: rel_path(f, scan_dir) for f in files}
    by_folder = defaultdict(list)
    for f in files:
        by_folder[f.parent].append(f)
    log("Найдено .ltx файлов: %d" % len(files))

    raw_children = defaultdict(set)
    raw_parents = defaultdict(set)
    missing = []

    for parent in files:
        try:
            text = read_text_safe(parent)
        except Exception as e:
            log("Не удалось прочитать %s: %s" % (parent, e))
            continue
        for inc in extract_includes(text):
            targets = resolve_include_targets(inc, parent)
            if not targets:
                missing.append((parent, inc))
                continue
            for child in targets:
                if child == parent or child not in file_set:
                    missing.append((parent, inc))
                    continue
                raw_children[parent].add(child)
                raw_parents[child].add(parent)

    log("Include-связей: %d; пропавших: %d"
        % (sum(len(v) for v in raw_children.values()), len(missing)))

    provisional = [f for f in files if not raw_parents.get(f)]
    attached = 0
    for root in provisional:
        for mod in get_dltx_mods_for_root(root, by_folder.get(root.parent, [])):
            if mod != root and not raw_parents.get(mod):
                raw_children[root].add(mod)
                raw_parents[mod].add(root)
                attached += 1
    log("DLTX mod_*.ltx присоединено к корням: %d" % attached)

    final_roots = [f for f in files if not raw_parents.get(f)]
    if system_root and system_root in file_set and system_root not in final_roots:
        final_roots.append(system_root)
    final_roots.sort(key=lambda p: (0 if p == system_root else 1,
                                    rel_paths.get(p, str(p)).lower()))

    display_children = defaultdict(list)
    node_parent = {}
    tree_root = {}
    tree_files = defaultdict(list)

    def assign(root):
        stack = [(root, None)]
        while stack:
            node, par = stack.pop()
            if node in tree_root:
                continue
            tree_root[node] = root
            tree_files[root].append(node)
            if par is not None:
                node_parent[node] = par
                display_children[par].append(node)
            kids = sorted(raw_children.get(node, set()),
                          key=lambda x: rel_paths.get(x, str(x)).lower(),
                          reverse=True)
            for ch in kids:
                if ch not in tree_root:
                    stack.append((ch, node))

    for r in final_roots:
        assign(r)
    for node in files:                      # циклы / сироты
        if node not in tree_root:
            final_roots.append(node)
            assign(node)

    for p in list(display_children.keys()):
        display_children[p].sort(key=lambda x: rel_paths.get(x, str(x)).lower())

    actual_roots = [r for r in final_roots if tree_root.get(r) == r]
    actual_roots.sort(key=lambda x: (0 if x == system_root else 1,
                                     rel_paths.get(x, str(x)).lower()))

    subtree = {}
    for root in actual_roots:
        stack = [(root, False)]
        while stack:
            node, visited = stack.pop()
            if visited:
                subtree[node] = 1 + sum(subtree.get(c, 1)
                                        for c in display_children.get(node, []))
            elif node not in subtree:
                stack.append((node, True))
                for c in display_children.get(node, []):
                    if c not in subtree:
                        stack.append((c, False))

    return LtxForest(
        scan_dir=str(scan_dir),
        system_root=str(system_root) if system_root else "",
        total_files=len(files),
        files=files,
        rel_paths=rel_paths,
        roots=actual_roots,
        roots_set=set(actual_roots),
        display_children=dict(display_children),
        node_parent=node_parent,
        subtree_counts=subtree,
        system_tree_files=len(tree_files.get(system_root, [])) if system_root else 0,
    )


# ---------------------------------------------------------------- запросы
def node_sort_key(forest, path, col):
    rel = forest.rel_paths.get(path, str(path)).lower()
    if col == "files":
        return (forest.subtree_counts.get(path, 1), rel)
    if col == "children":
        return (len(forest.display_children.get(path, [])), rel)
    return (rel, forest.subtree_counts.get(path, 1))


def sort_nodes(forest, nodes, col="path", reverse=False):
    return sorted(nodes, key=lambda p: node_sort_key(forest, p, col),
                  reverse=reverse)


def search_nodes(forest, text):
    text = (text or "").strip().lower()
    if not text:
        return []
    out = [p for p in forest.files
           if text in p.name.lower()
           or text in forest.rel_paths.get(p, "").lower()]
    return sort_nodes(forest, out, "path")


def build_source_map(forest) -> dict:
    mp = {}
    for f in forest.files:
        rel = forest.rel_paths.get(f, str(f))
        mp[os.path.splitext(str(f).lower())[0]] = rel
        b = os.path.splitext(f.name.lower())[0]
        if b not in mp:
            mp[b] = rel
    return mp


def pretty_source(source, source_map) -> str:
    if not source:
        return "?"
    s = str(source).lower()
    if s in source_map:
        return source_map[s]
    s2 = os.path.splitext(s)[0]
    if s2 in source_map:
        return source_map[s2]
    b = os.path.basename(s2)
    return source_map.get(b, source)


def generate_report(forest, col="path", reverse=False) -> str:
    lines = [
        "Scan dir: %s" % forest.scan_dir,
        "System.ltx: %s" % forest.system_root,
        "Resolver sections: %s" % forest.resolver_sections,
        "Total .ltx files: %d" % forest.total_files,
        "Files in system.ltx tree: %d" % forest.system_tree_files,
        "Top-level roots: %d" % len(forest.roots),
        "",
        "root;files_in_tree;direct_children",
    ]
    for p in sort_nodes(forest, forest.roots, col, reverse):
        lines.append("%s;%d;%d" % (
            forest.rel_paths.get(p, str(p)),
            forest.subtree_counts.get(p, 1),
            len(forest.display_children.get(p, []))))
    return "\n".join(lines)


# ---------------------------------------------------------------- сборка
class Assembler:
    """Обёртка над resolver4: проход, сборка текста, запись файла."""

    def __init__(self, resolver_script):
        self.resolver_script = Path(resolver_script)
        self._mod = load_resolver_module(self.resolver_script)
        cls = getattr(self._mod, "LtxResolver", None)
        if cls is None:
            raise RuntimeError("В %s нет класса LtxResolver" % self.resolver_script)
        self._cls = cls

    def _make(self, root_path):
        try:
            return self._cls(str(root_path), use_cache=False, verbose=False)
        except TypeError:
            return self._cls(str(root_path), verbose=False)

    def pass_only(self, root_path) -> int:
        r = self._make(root_path)
        r.load()
        return len(getattr(r, "data", []))

    def assemble_text(self, root_path, source_map=None) -> str:
        r = self._make(root_path)
        r.load()
        sm = source_map or {}
        lines = ["; Assembled root: %s" % root_path,
                 "; Resolver module: %s" % self.resolver_script, ""]
        for sec_name, sect in r.data:
            sec_src = ""
            for it in sect.data:
                if getattr(it, "filename", ""):
                    sec_src = pretty_source(it.filename, sm)
                    break
            lines.append("[%s] ; <= %s" % (sec_name, sec_src) if sec_src
                         else "[%s]" % sec_name)
            for it in sect.data:
                key = str(getattr(it, "first", "") or "")
                val = getattr(it, "second", "")
                if val is None:
                    val_s = ""
                elif hasattr(val, "name") and type(val).__name__ == "DLTXToken":
                    val_s = "<%s>" % val.name
                else:
                    val_s = str(val)
                src = pretty_source(getattr(it, "filename", ""), sm)
                if "\n" in val_s or "\r" in val_s:
                    lines.append("%s = %s" % (key, val_s))
                    lines.append("; <= %s" % src)
                elif val_s == "":
                    lines.append("%s = ; <= %s" % (key, src))
                else:
                    lines.append("%s = %s ; <= %s" % (key, val_s, src))
            lines.append("")
        return "\n".join(lines)

    def assemble_to_file(self, root_path, out_dir, source_map=None) -> Path:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        rp = Path(root_path)
        safe = lambda s: "".join(c if c.isalnum() or c in "_-" else "_"
                                 for c in s).lower()
        out = out_dir / ("%s__%s__assembled.ltx"
                         % (safe(rp.parent.name), safe(rp.stem)))
        out.write_text(self.assemble_text(rp, source_map), encoding="utf-8")
        return out
# -- coding: utf-8 --
"""
Python-резолвер подсистемы загрузки LTX/DLTX движка XRAY.
Полностью повторяет наблюдаемое поведение CInifile из
src/xrCore/Xr_ini.cpp и src/xrCore/xr_ini.h репозитория
themrdemonized/xray-monolith (ветка all-in-one-vs2022-wpo).

ИСПРАВЛЕНИЯ относительно предыдущей версии:
  [FIX-1] Убран is_delete из ключа сортировки в _sort_and_filter_section.
          Оригинал сортирует ТОЛЬКО по (ключ, depth, insertionIndex).
          Прежнее поведение ломало оператор !key (удаление ключа).
  [FIX-2] Пустые значения ('key =' и 'key' без '=') теперь оба дают None (NULL),
          как в оригинале: I.second = (str2[0] ? str2 : NULL).
          Прежнее поведение ('' для 'key =') ломало пропуск пустых модификаций >/<.
  [FIX-3] Секции @[...] создаются ОДИН раз, ПОСЛЕ загрузки мод-файлов,
          как в оригинале (конец LTXLoad). Убрано дублирование.
  [FIX-4] Родители наследования приводятся к нижнему регистру
          для консистентности с общим правилом движка.
"""
import os
import re
from collections import OrderedDict
from enum import Enum


class DLTXToken(Enum):
    DELETE = "DELETE"
    EMPTY = "EMPTY"


DLTX_DELETE = DLTXToken.DELETE
DLTX_EMPTY = DLTXToken.EMPTY
MOD_DEPTH_STEP = -200


# ---------------------------------------------------------------- utilities
def _xr_strcmp(a, b):
    """xr_strcmp == strcmp (byte-wise), см. _std_extensions.h:205-208."""
    a = "" if a is None else a
    b = "" if b is None else b
    return (a > b) - (a < b)


def _key(s):
    """Ключ сортировки, эквивалентный побайтовому strcmp для ASCII."""
    return s.encode("latin-1", "replace") if isinstance(s, str) else s


def _trim(s):
    """_Trim: удаление пробельных символов с двух сторон (_std_extensions.h)."""
    return s.strip()


def pattern_match(name, mask):
    """
    FS-маска с '*' и '?' (PatternMatch, LocatorAPI.cpp; используется в file_list,
    LocatorAPI.cpp:1030-1041). Регистронезависимо: имена файлов в FS_FileSet
    приводятся к lower-case (LocatorAPI_defs.h:63 'low-case name').
    """
    rx = "^" + "".join(
        ".*" if c == "*" else "." if c == "?" else re.escape(c)
        for c in mask.lower()
    ) + "$"
    return re.match(rx, name.lower()) is not None


def _get_item_count(s):
    """_GetItemCount: число элементов списка через запятую (xrstring.h)."""
    if not s:
        return 0
    n = 1
    inside = False
    for ch in s:
        if ch == '"':
            inside = not inside
        elif ch == "," and not inside:
            n += 1
    return n


def _get_item(s, idx):
    """_GetItem(...,','): i-й элемент списка, кавычки снимаются (xrstring.h)."""
    parts = []
    cur = []
    inside = False
    for ch in s:
        if ch == '"':
            inside = not inside
            continue
        if ch == "," and not inside:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    if idx >= len(parts):
        return ""
    return _trim(parts[idx])


def _parse_value(src):
    """
    _parse (Xr_ini.cpp:38-67): схлопывает пробелы вне кавычек;
    возвращает (строка, bInsideSTR - нечётное число кавычек).
    """
    out = []
    inside = False
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            if inside:
                out.append(ch)
                i += 1
                continue
            while i < n and src[i].isspace():
                i += 1
            if i >= n:
                break
            ch = src[i]
        if ch == '"':
            inside = not inside
            out.append(ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out), inside


# ---------------------------------------------------------------- data model
class Item:
    """CInifile::Item (xr_ini.h:12-33): first/second/filename/depth/insertionIndex."""
    __slots__ = ("first", "second", "filename", "depth", "insertion_index")

    def __init__(self, first=None, second=None, filename="", depth=0, insertion_index=0):
        self.first = first
        self.second = second
        self.filename = filename
        self.depth = depth
        self.insertion_index = insertion_index


class Sect:
    """CInifile::Sect (xr_ini.h:68-74): { Name; xr_vector<Item> Data }."""
    __slots__ = ("name", "data")

    def __init__(self, name=""):
        self.name = name
        self.data = []

    def __iter__(self):
        return iter(self.data)

    def __len__(self):
        return len(self.data)


# ---------------------------------------------------------------- resolver
class LtxResolver:
    """Эквивалент одного экземпляра CInifile (корневой .ltx файла)."""

    def __init__(self, root_path, gamedata_root=None, allow_include_func=None,
                 use_cache=True, verbose=False):
        self.root_path = os.path.normpath(root_path)
        self.file_name = os.path.basename(self.root_path)
        self.allow_include_func = allow_include_func
        self.use_cache = use_cache
        self.verbose = verbose
        self.override_to_filenames = {}
        self.section_to_filename = {}
        self.sections_to_delete = set()
        self.base_parent_data_map = {}
        self.base_data = OrderedDict()
        self.override_parent_data_map = {}
        self.override_data = OrderedDict()
        self.override_modify_list_data = {}
        self.data = []
        self.warnings = []
        self._cache = {}
        self.print_dltx_warnings = False

    def _log(self, msg):
        if self.verbose:
            print(msg)

    def _warn(self, msg):
        self.warnings.append(msg)
        self._log("~[DLTX] WARNING: " + msg)

    @staticmethod
    def _file_list(folder, mask):
        """
        FS.file_list(FS_FileSet&...) — упорядоченное множество по xr_strcmp
        от lower-case имени (LocatorAPI.cpp:997-1055, LocatorAPI_defs.h:73-78).
        """
        result = set()
        if not os.path.isdir(folder):
            return []
        for entry in os.listdir(folder):
            full = os.path.join(folder, entry)
            if not os.path.isfile(full):
                continue
            low = entry.lower()
            if pattern_match(low, mask.lower()):
                result.add(low)
        return sorted(result, key=_key)

    def _insert_item(self, tgt, item):
        """
        CInifile::insert_item (Xr_ini.cpp:235-256):
          * префикс '>' (Insert) или '<' (Remove) у ключа — операция над
            CSV-списком, уходит в OverrideModifyListData (xr_ini.h:214-218);
          * иначе — push_back с присвоением insertionIndex = текущий размер.
        """
        key = item.first or ""
        if key:
            op = key[0]
            if op in (">", "<"):
                # CSV-операции НЕ добавляются в tgt.data.
                # Они только в override_modify_list_data.
                lst = self.override_modify_list_data.setdefault(tgt.name, [])
                lst.append(item)
                item.insertion_index = len(lst)
                return
        item.insertion_index = len(tgt.data)
        tgt.data.append(item)

    def _stash_current_section(self, current_base, current_override, current_file_name):
        """CInifile::StashCurrentSection (Xr_ini.cpp:365-408). Точный порт."""
        if current_base is not None:
            existing = self.base_data.get(current_base.name)
            if existing is not None:
                raise RuntimeError(
                    "[DLTX] Duplicate section '%s' wasn't marked as an override. "
                    "Override section by prefixing it with '!' (![%s]). "
                    "Check this file and its DLTX mods: %s, file with section: %s, "
                    "file with duplicate: %s" % (
                        current_base.name, current_base.name, self.file_name,
                        self.section_to_filename.get(current_base.name, "?"),
                        current_file_name))
            self.base_data[current_base.name] = current_base
            self.section_to_filename[current_base.name] = current_file_name
        if current_override is not None:
            existing = self.override_data.get(current_override.name)
            if existing is not None:
                for it in current_override.data:
                    self._insert_item(existing, it)
                self._insert_into_map(self.override_to_filenames, existing.name, current_file_name)
            else:
                self.override_data[current_override.name] = current_override
                if current_override.name not in self.base_data:
                    self._warn("Attempted to override section '%s', which doesn't "
                               "exist. Ensure that a base section with the same "
                               "name is loaded first. Check %s, mod file %s" % (
                                   current_override.name, self.file_name,
                                   current_file_name))
                    self._insert_into_map(self.override_to_filenames,
                                          current_override.name, current_file_name)

    @staticmethod
    def _insert_into_map(mp, key, fname):
        """InsertIntoMap<K,V,xr_string> (Xr_ini.cpp:~120-133)."""
        lst = mp.setdefault(key, [])
        if fname not in lst:
            lst.append(fname)

    def _load_file(self, fn, inc_path, name, current_file_name, depth):
        """CInifile::loadFile (Xr_ini.cpp:330-363)."""
        if self.allow_include_func is not None and not self.allow_include_func(fn):
            return
        if not os.path.isfile(fn):
            raise FileNotFoundError("Can't find include file: %s" % name)
        prev_name = current_file_name[0]
        current_file_name[0] = name
        self._ltx_load(fn, inc_path, False, current_file_name, depth)
        current_file_name[0] = prev_name

    def _ltx_load(self, reader_path, path, b_is_root_file, current_file_name, depth):
        """
        CInifile::LTXLoad (Xr_ini.cpp:454-906) — однопроходный парсер.
        """
        current_base = None
        current_override = None
        sections_marked_for_create = OrderedDict()
        b_has_loaded_mod_files = False
        lines = self._read_lines(reader_path)
        i = 0
        n = len(lines)

        while True:
            # === Достижение конца файла ===
            if i >= n:
                if b_is_root_file and not b_has_loaded_mod_files:
                    # Оригинал: при EOF корневого файла сначала сташим текущую
                    # секцию, затем загружаем мод-файлы. Создание @[...]-секций
                    # происходит ПОСЛЕ цикла (после загрузки мод-файлов). [FIX-3]
                    b_has_loaded_mod_files = True
                    self._stash_current_section(current_base, current_override, current_file_name[0])
                    current_base = current_override = None
                    self._load_mod_files(current_file_name, path)
                    # НЕ создаём секции здесь — это делается после цикла. [FIX-3]
                    break
                else:
                    break

            line = _trim(lines[i])
            i += 1

            # комментарии: ';' и '//' (Xr_ini.cpp:618-651)
            comm = line.find(";")
            comm1 = line.find("/")
            if comm1 != -1 and comm1 + 1 < len(line) and line[comm1 + 1] == "/" \
                    and (comm == -1 or comm1 < comm):
                comm = comm1
            if comm != -1:
                q1 = line.find('"')
                in_quot = False
                if q1 != -1 and q1 < comm:
                    q2 = line.find('"', q1 + 1)
                    if q2 != -1 and q2 > comm:
                        in_quot = True
                if not in_quot:
                    line = _trim(line[:comm])

            # #include (Xr_ini.cpp:670-720)
            if line and line[0] == "#" and "#include" in line:
                match = re.match(r'#include\s+"([^"]+)"', line)
                if match:
                    inc_name = match.group(1)
                else:
                    inc_name = _get_item(line, 1)
                if inc_name:
                    # Нормализуем слеши для единообразия.
                    inc_name_norm = inc_name.replace("\\", "/")
                    # Разделяем на каталог и имя файла/маску.
                    inc_dir, inc_file = os.path.split(inc_name_norm)
                    # Полный каталог для поиска.
                    search_dir = os.path.join(path, inc_dir) if inc_dir else path
                    # Проверяем наличие символов маски в имени файла.
                    if "*" in inc_file or "?" in inc_file:
                        # Маска: перечисляем файлы.
                        for nm in self._file_list(search_dir, inc_file):
                            full_fn = os.path.join(search_dir, nm)
                            # Каталог для вложенных инклудов - тот же, где лежит файл.
                            self._load_file(full_fn, search_dir, full_fn, current_file_name, depth + 1)
                    else:
                        # Обычный файл.
                        full_fn = os.path.join(search_dir, inc_file)
                        # Каталог для вложенных инклудов - каталог файла.
                        self._load_file(full_fn, search_dir, full_fn, current_file_name, depth + 1)
                continue

            # !![sec] — удаление секции (Xr_ini.cpp:721-740)
            if line and line.startswith("!!["):
                self._stash_current_section(current_base, current_override, current_file_name[0])
                current_base = current_override = None
                close = line.find("]")
                sec = line[3:close].lower()
                self._log("[DLTX] [%s] Encountered %s, mark section to delete" % (self.file_name, line))
                self.sections_to_delete.add(sec)
                continue

            # [sec], ![sec], @[sec], возможно [:parent1,parent2]
            is_override = line.startswith("![")
            is_safe = line.startswith("@[")
            if line and (line[0] == "[" or is_override or is_safe):
                self._stash_current_section(current_base, current_override, current_file_name[0])
                current_base = current_override = None
                start = 2 if (is_override or is_safe) else 1
                close_bracket = line.find("]", 2) if (is_override or is_safe) else line.find("]")
                if close_bracket == -1:
                    raise RuntimeError("Bad ini section found: %s" % line)
                sec_full = line[start:close_bracket]
                sec_name = sec_full.lower()
                b_is_override = False
                if is_override:
                    b_is_override = True
                elif is_safe:
                    b_is_override = True
                    if sec_name not in self.base_data:
                        sections_marked_for_create[sec_name] = True
                sect = Sect(sec_name)
                if b_is_override:
                    current_override = sect
                else:
                    current_base = sect
                # наследование: strstr(str, "]:") — ТОЛЬКО непосредственно "]" + ":"
                inherit_pos = line.find("]:")
                if inherit_pos != -1:
                    parents_str = line[inherit_pos + 2:]
                    # [FIX-4] Родители приводятся к нижнему регистру
                    # для консистентности с общим правилом движка
                    # (все имена секций — в нижнем регистре).
                    parents = [_trim(_get_item(parents_str, k)).lower()
                               for k in range(_get_item_count(parents_str))]
                    parents = [p for p in parents if p]
                    target_map = (self.override_parent_data_map if b_is_override
                                   else self.base_parent_data_map)
                    cur = target_map.setdefault(sec_name, [])
                    self._merge_parent_set(cur, parents, True)
                continue

            # key = value
            if line and line[0] != ";":
                b_is_delete = line[0] == "!"
                name_part = line[1:] if b_is_delete else line
                eq = name_part.find("=")
                if eq != -1:
                    name = _trim(name_part[:eq])
                    raw_value = name_part[eq + 1:]
                    value, inside = _parse_value(raw_value)
                    # многострочные значения с нечётной кавычкой (Xr_ini.cpp:827-851)
                    while inside and i < n:
                        raw_value += "\r\n" + lines[i]
                        i += 1
                        value, inside = _parse_value(raw_value)
                else:
                    name = _trim(name_part)
                    value = ""

                item = Item()
                item.first = name if name else None
                if item.first is None:
                    self._log("~[DLTX] WARNING: Malformed line %s in file %s" %
                              (line, current_file_name[0]))
                    continue
                # [FIX-2] Оригинал: I.second = bIsDelete ? DLTX_DELETE
                #                            : (str2[0] ? str2 : NULL);
                # И 'key =', и 'key' без '=' дают пустой str2, т.е. оба -> NULL.
                if b_is_delete:
                    item.second = DLTX_DELETE
                else:
                    item.second = value if value else None
                item.filename = os.path.splitext(current_file_name[0].lower())[0]
                item.depth = depth
                if current_base is not None:
                    self._insert_item(current_base, Item(item.first, item.second,
                                                         item.filename, depth))
                if current_override is not None:
                    self._insert_item(current_override, Item(item.first, item.second,
                                                             item.filename, depth))
                continue

        # === После цикла (для всех файлов, включая корневой) ===
        self._stash_current_section(current_base, current_override, current_file_name[0])

        # [FIX-3] Создание пустых @[...]-секций ОДИН раз, ПОСЛЕ загрузки
        # мод-файлов — точно как в оригинале (конец LTXLoad).
        for sec_name in sections_marked_for_create:
            if sec_name not in self.base_data:
                s = Sect(sec_name)
                self.base_data[sec_name] = s
                fnames = self.override_to_filenames.setdefault(sec_name, [])
                fname = current_file_name[0]
                if fname not in fnames:
                    fnames.append(fname)
                self.section_to_filename[sec_name] = fname

    def _load_mod_files(self, current_file_name, folder):
        """
        Автозагрузка mod_system_* / mod_<root>_*.ltx (Xr_ini.cpp:546-614).
        """
        stem = os.path.splitext(current_file_name[0])[0].lower()
        ambiguous = {os.path.splitext(f)[0]
                     for f in self._file_list(folder, stem + "_*.ltx")}
        mod_files = self._file_list(folder, "mod_" + stem + "_*.ltx")
        d = MOD_DEPTH_STEP
        dt = MOD_DEPTH_STEP
        for mod_name in mod_files:
            skip = False
            for amb in ambiguous:
                if amb == stem:
                    continue
                pat = "mod_" + amb + "_*.ltx"
                head = "mod_" + amb + "_"
                if mod_name.startswith(head) and len(mod_name) > len(head) + 4 \
                        and pattern_match(mod_name, pat):
                    skip = True
                    break
            if skip:
                continue
            self._load_file(os.path.join(folder, mod_name), folder,
                            mod_name, current_file_name, d)
            d += dt

    @staticmethod
    def _merge_parent_set(parents_base, parents_override, include_removers):
        """CInifile::MergeParentSet (Xr_ini.cpp:300-328). Родители с '!' удаляются."""
        for cur in parents_override:
            is_removal = cur.startswith("!")
            stale = ("" if is_removal else "!") + (cur[1:] if is_removal else cur)
            parents_base[:] = [p for p in parents_base if p != stale]
            if include_removers or not is_removal:
                parents_base.append(cur)

    def _sort_and_filter_section(self, sect):
        """
        CInifile::SortAndFilterSection (Xr_ini.cpp:410-451):
        сортировка (ключ ↑, depth ↑, insertionIndex ↓), затем оставляется
        ПЕРВЫЙ элемент каждой группы равных ключей.

        [FIX-1] is_delete НЕ участвует в сортировке. Оригинал сортирует
        ТОЛЬКО по (ключ, depth, insertionIndex). Это критично для работы !key:
        мод-файл с отрицательным depth должен побеждать базовое значение.
        """
        if len(sect.data) < 2:
            return

        def sort_key(it):
            # Оригинал: ключ ↑, depth ↑, insertionIndex ↓
            return (_key(it.first or ""), it.depth, -it.insertion_index)

        sect.data.sort(key=sort_key)
        result = []
        j = 0
        while j < len(sect.data):
            k = sect.data[j].first
            result.append(sect.data[j])
            j += 1
            while j < len(sect.data) and sect.data[j].first == k:
                j += 1
        sect.data = result
        # CSV-операции (>/<) НЕ фильтруются здесь (они в _evaluate_section).

    def _merge_sections(self, base_items, override_items, deleted_items,
                        is_merging_base_and_mod):
        """
        CInifile::MergeSections (Xr_ini.cpp:908-1002). Точный порт merge-цикла.
        """
        result = []
        b = sorted(base_items, key=lambda it: it.first or "")
        o = sorted(override_items, key=lambda it: it.first or "")
        bi = oi = 0
        while bi < len(b) or oi < len(o):
            if bi == len(b):
                ov = o[oi]
                if ov.second is DLTX_DELETE:
                    if is_merging_base_and_mod:
                        deleted_items.add(ov.first)
                else:
                    result.append(ov)
                oi += 1
                continue
            if oi == len(o):
                result.append(b[bi])
                bi += 1
                continue
            cmp = _xr_strcmp(b[bi].first or "", o[oi].first or "")
            if cmp < 0:
                result.append(b[bi]); bi += 1
            elif cmp > 0:
                ov = o[oi]
                if ov.second is DLTX_DELETE:
                    if is_merging_base_and_mod:
                        deleted_items.add(ov.first)
                else:
                    result.append(ov)
                oi += 1
            else:
                ov = o[oi]
                if ov.second is DLTX_DELETE:
                    if is_merging_base_and_mod:
                        deleted_items.add(ov.first)
                    else:
                        result.append(b[bi])
                else:
                    result.append(ov)
                oi += 1
                bi += 1
        return result

    def _evaluate_section(self, section_name, resolved_cache, recursion_stack):
        """
        CInifile::EvaluateSection (Xr_ini.cpp:1004-1295).
        """
        if section_name in resolved_cache:
            return resolved_cache[section_name]
        if section_name in recursion_stack:
            raise RuntimeError("[DLTX] Section '%s' has cyclical dependencies. Cycle: %s"
                               % (section_name, " -> ".join(recursion_stack)))
        recursion_stack.append(section_name)
        base_parents = self.base_parent_data_map.get(section_name)
        override_parents = self.override_parent_data_map.get(section_name)
        if override_parents is not None and base_parents is None:
            base_parents = []
            self.base_parent_data_map[section_name] = base_parents
            self._merge_parent_set(base_parents, list(override_parents), False)
        elif base_parents is not None and override_parents is not None:
            self._merge_parent_set(base_parents, list(override_parents), False)
        resolved_parents = []
        deleted_items = set()
        for parent in (base_parents or []):
            pname = parent[1:] if parent.startswith("!") else parent
            if pname not in self.base_data:
                if pname in self.override_data:
                    self._warn("Section '%s' has parent '%s' that is defined as Override. "
                               "Creating parent for backwards compatibility." % (section_name, pname))
                    s = Sect(pname)
                    self.base_data[pname] = s
                else:
                    if self.print_dltx_warnings:
                        self._warn("Section '%s' inherits from non-existent section '%s'. "
                                   "Creating fallback empty parent section." % (section_name, pname))
                    s = Sect(pname)
                    self.base_data[pname] = s
            parent_data = self._evaluate_section(pname, resolved_cache, recursion_stack)
            resolved_parents = self._merge_sections(resolved_parents, parent_data,
                                                    deleted_items, False)
        resolved_base_and_mods = list(self.base_data[section_name].data) \
            if section_name in self.base_data else []
        ov = self.override_data.get(section_name)
        if ov is not None:
            resolved_base_and_mods = self._merge_sections(resolved_base_and_mods,
                                                          ov.data, deleted_items, True)
            del self.override_data[section_name]
        current_result = self._merge_sections(resolved_parents, resolved_base_and_mods,
                                              deleted_items, False)
        sect_obj = Sect(section_name)
        sect_obj.data = current_result
        current_result = sect_obj
        # операции > / < над CSV-списками (Xr_ini.cpp:1167-1290)
        mods = self.override_modify_list_data.get(section_name)
        if mods:
            mods_sorted = sorted(mods, key=lambda it: (_key(it.first[1:]),
                                                       it.insertion_index))
            result = []
            cur = sorted(current_result, key=lambda it: _key(it.first or ""))
            di = mi = 0
            while di < len(cur) or mi < len(mods_sorted):
                # Оригинал: if (mod_it->second == NULL) { mod_it++; continue; }
                # [FIX-2] Теперь пустые значения -> None, и они корректно пропускаются.
                if mi < len(mods_sorted) and mods_sorted[mi].second is None:
                    mi += 1
                    continue
                if mi < len(mods_sorted) and di < len(cur):
                    mod_key = mods_sorted[mi].first[1:]
                    c = _xr_strcmp(cur[di].first, mod_key)
                    active_key = cur[di].first if c <= 0 else mod_key
                elif mi < len(mods_sorted):
                    active_key = mods_sorted[mi].first[1:]
                else:
                    active_key = cur[di].first
                existing = None
                if di < len(cur) and cur[di].first == active_key:
                    existing = cur[di]
                if mi < len(mods_sorted) and mods_sorted[mi].first[1:] == active_key:
                    working = None
                    exists_in_output = False
                    if existing is not None:
                        working = Item(existing.first, existing.second,
                                       existing.filename, existing.depth)
                        exists_in_output = True
                        di += 1
                    else:
                        if active_key not in deleted_items:
                            working = Item(active_key, "", "")
                            exists_in_output = True
                    while mi < len(mods_sorted) and mods_sorted[mi].first[1:] == active_key:
                        m = mods_sorted[mi]
                        # Оригинал: if (exists_in_output && mod_it->second != NULL)
                        if exists_in_output and m.second is not None:
                            op = m.first[0]
                            items_vec = self._split_list(working.second
                                                           if isinstance(
                                                               working.second, str)
                                                           else "")
                            add_vec = self._split_list(m.second)
                            if op == ">":
                                items_vec.extend(add_vec)
                            elif op == "<":
                                items_vec = [x for x in items_vec if x not in add_vec]
                            working.second = self._join_list(items_vec)
                            working.filename = m.filename
                        mi += 1
                    if exists_in_output and working.second:
                        result.append(working)
                else:
                    if existing is not None:
                        result.append(existing)
                        di += 1
            current_result = result
        recursion_stack.pop()
        final_sect = Sect(section_name)
        final_sect.data = list(current_result) if not isinstance(current_result, Sect) \
            else current_result.data
        resolved_cache[section_name] = final_sect
        return final_sect

    @staticmethod
    def _split_list(s, delimiter=","):
        """split_list (Xr_ini.cpp:1101-1138): trim, пропуск пустых."""
        if not s:
            return []
        return [t.strip() for t in s.split(delimiter) if t.strip()]

    @staticmethod
    def _join_list(vec, delimiter=","):
        """join_list (Xr_ini.cpp:1141-1165): склейка БЕЗ пробела после разделителя."""
        return delimiter.join(vec)

    def load(self):
        """CInifile::Load (Xr_ini.cpp:1297-1420) + кэш конструктора (189-206)."""
        cache_key = self.root_path.lower()
        if self.use_cache and self.file_name:
            if cache_key in self._cache:
                self._log("[DLTX] [%s] Found data in cache" % self.file_name)
                self.data = self._cache[cache_key]
                return self
        current_file_name = [self.file_name]
        folder = os.path.dirname(self.root_path)
        self._ltx_load(self.root_path, folder, True, current_file_name, 0)
        for sect in self.base_data.values():
            self._sort_and_filter_section(sect)
        for sect in self.override_data.values():
            self._sort_and_filter_section(sect)
        resolved = {}
        for name in sorted(list(self.base_data.keys()), key=_key):
            self._evaluate_section(name, resolved, [])
        for s in sorted(self.sections_to_delete, key=_key):
            self._log("[DLTX] [%s] Found section %s to delete" % (self.file_name, s))
            if s in resolved:
                del resolved[s]
                if s in self.override_data:
                    del self.override_data[s]
        self.data = sorted(((k, v) for k, v in resolved.items()),
                           key=lambda kv: _key(kv[0]))
        if self.use_cache and self.file_name:
            self._cache[cache_key] = self.data
        # cleanup
        self.override_to_filenames.clear()
        self.section_to_filename.clear()
        self.sections_to_delete.clear()
        self.base_parent_data_map.clear()
        self.base_data.clear()
        self.override_parent_data_map.clear()
        self.override_data.clear()
        self.override_modify_list_data.clear()
        return self

    def section_exist(self, name):
        name = name.lower()
        lo, hi = 0, len(self.data)
        while lo < hi:
            mid = (lo + hi) // 2
            if _xr_strcmp(self.data[mid][0], name) < 0:
                lo = mid + 1
            else:
                hi = mid
        return lo < len(self.data) and self.data[lo][0] == name

    def r_section(self, name):
        """Бинарный поиск по отсортированному Root DATA."""
        name = name.lower()
        lo, hi = 0, len(self.data)
        while lo < hi:
            mid = (lo + hi) // 2
            if _xr_strcmp(self.data[mid][0], name) < 0:
                lo = mid + 1
            else:
                hi = mid
        if lo < len(self.data) and self.data[lo][0] == name:
            return self.data[lo][1]
        return None

    def line_exist(self, sec, key):
        sect = self.r_section(sec)
        if sect is None:
            return False
        for it in sect.data:
            if it.first == key:
                return True
        return False

    def r_string(self, sec, key):
        sect = self.r_section(sec)
        if sect is not None:
            for it in sect.data:
                if it.first == key:
                    return it.second
        raise KeyError("Cannot find line %s:%s" % (sec, key))

    def as_dict(self):
        out = OrderedDict()
        for name, sect in self.data:
            out[name] = OrderedDict((it.first, it.second) for it in sect.data)
        return out

    @staticmethod
    def _read_lines(path):
        """Чтение строк файла. Движок читает байты (IReader::r_string);
        игровые .ltx часто в Windows-1251, поэтому: сначала UTF-8 строго,
        при UnicodeDecodeError — cp1251 с заменой; BOM снимается."""
        with open(path, "rb") as f:
            raw = f.read()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("windows-1251", errors="replace")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text.split("\n")
# -*- coding: utf-8 -*-
"""LtxResolverAdapter — адаптер к внешнему резолверу Resolver4.py."""

import hashlib
from pathlib import Path

from core.models import LineSource
from core.patching.ltx.multiline import analyze_multiline_edit
from core.resolver.attribution import pretty_source
from core.tools.text import safe_filename_component
from core.tools.fs import (
    find_resolver_script,
    load_resolver_module,
)


def _log(log, message):
    if log is None:
        return
    try:
        log(message)
    except Exception:
        pass


class LtxResolverAdapter:
    """
    Адаптер к внешнему инструменту разрешения конфигов.

    Внешний инструмент:
        core/Resolver4.py

    Адаптер не реализует саму логику движка.
    Он только подключает внешний резолвер и нормализует работу с ним.
    """

    def __init__(self, resolver_script=None, cache=None):
        if resolver_script is None:
            resolver_script = find_resolver_script()

        if resolver_script is None:
            raise FileNotFoundError("Resolver4.py не найден")

        self.resolver_script = Path(resolver_script)
        self.cache = cache

        self._mod = load_resolver_module(self.resolver_script)
        self._resolved_change_cache = {}

        cls = getattr(self._mod, "LtxResolver", None)
        if cls is None:
            raise RuntimeError(
                "В %s нет класса LtxResolver" % self.resolver_script
            )

        self._cls = cls

    def _make(self, root_path):
        """
        Создаёт экземпляр внешнего резолвера.

        Поддерживаются оба варианта сигнатуры:
            LtxResolver(path, use_cache=False, verbose=False)
            LtxResolver(path, verbose=False)
        """
        try:
            return self._cls(
                str(root_path),
                use_cache=False,
                verbose=False,
            )
        except TypeError:
            return self._cls(
                str(root_path),
                verbose=False,
            )

    def _make_with_overlay(self, root_path, overlay=None, log=None):
        """Создать резолвер и наложить виртуальные файлы проекта."""
        resolver = self._make(root_path)

        source_files = getattr(overlay, "source_files", None)
        if source_files is None:
            source_files = overlay
        if not source_files:
            return resolver

        import os

        from core.tools.fs import pattern_match

        files = {}
        new_ltx = []
        for path, text in source_files.items():
            current = os.path.normcase(os.path.abspath(str(path)))
            files[current] = str(text or "")
            if str(path).lower().endswith(".ltx"):
                try:
                    if not os.path.isfile(str(path)):
                        new_ltx.append(str(path))
                except OSError:
                    new_ltx.append(str(path))

        original_read_lines = resolver._read_lines
        original_load_file = resolver._load_file
        original_file_list = resolver._file_list
        original_load_mods = resolver._load_mod_files
        depth_step = getattr(self._mod, "MOD_DEPTH_STEP", -200)
        loaded_virtual = set()

        def read_lines(path):
            current = os.path.normcase(os.path.abspath(str(path)))
            if current in files:
                return (
                    files[current]
                    .replace("\r\n", "\n")
                    .replace("\r", "\n")
                    .split("\n")
                )
            return original_read_lines(path)

        def load_file(fn, inc_path, name, current_file_name, depth):
            current = os.path.normcase(os.path.abspath(str(fn)))
            if current in files:
                if current in loaded_virtual:
                    return
                loaded_virtual.add(current)
                previous = current_file_name[0]
                current_file_name[0] = name
                resolver._ltx_load(
                    fn,
                    inc_path,
                    False,
                    current_file_name,
                    depth,
                )
                current_file_name[0] = previous
                return
            return original_load_file(
                fn,
                inc_path,
                name,
                current_file_name,
                depth,
            )

        def file_list(folder, mask):
            result = list(original_file_list(folder, mask))
            folder_norm = os.path.normcase(
                os.path.abspath(str(folder))
            )
            for path in files:
                if os.path.dirname(path) != folder_norm:
                    continue
                name = os.path.basename(path)
                if pattern_match(name, mask):
                    result.append(name.lower())
            return sorted(set(result), key=lambda value: value.lower())

        root_norm = os.path.normcase(os.path.abspath(str(root_path)))

        def load_mods(current_file_name, folder):
            original_load_mods(current_file_name, folder)

            # Новые LTX-файлы проекта не существуют на диске и потому не
            # попадают в обычный обход. Подключаем их как дополнительные
            # DLTX-патчи текущего корня.
            depth = depth_step
            for path in sorted(new_ltx, key=lambda value: value.lower()):
                current = os.path.normcase(os.path.abspath(path))
                name = os.path.basename(path)
                if current == root_norm:
                    _log(
                        log,
                        "DLTX overlay: %s — Пропущено "
                        "(это корневой файл)" % name,
                    )
                    continue
                if current in loaded_virtual:
                    _log(
                        log,
                        "DLTX overlay: %s — Пропущено "
                        "(уже загружен штатно)" % name,
                    )
                    continue
                resolver._load_file(
                    path,
                    os.path.dirname(path),
                    os.path.basename(path),
                    current_file_name,
                    depth,
                )
                _log(
                    log,
                    "DLTX overlay: %s — Выполнено" % name,
                )
                depth += depth_step

        resolver._read_lines = read_lines
        resolver._load_file = load_file
        resolver._file_list = file_list
        resolver._load_mod_files = load_mods
        return resolver

    def _resolved_changes(self, original_text, current_text,
                          line_sources=()):
        key = (
            hashlib.sha1(
                str(original_text or "").encode("utf-8", "replace")
            ).hexdigest(),
            hashlib.sha1(
                str(current_text or "").encode("utf-8", "replace")
            ).hexdigest(),
        )
        cached = self._resolved_change_cache.get(key)
        if cached is not None:
            return cached

        analysis = analyze_multiline_edit(
            original_text,
            current_text,
            line_sources=line_sources,
        )
        value = (analysis.changes, analysis.issues)
        self._resolved_change_cache[key] = value
        if len(self._resolved_change_cache) > 16:
            first = next(iter(self._resolved_change_cache))
            self._resolved_change_cache.pop(first, None)
        return value

    def _apply_resolved_overrides(self, resolver, overrides, log=None):
        if not overrides:
            return

        item_cls = getattr(self._mod, "Item", None)
        sect_cls = getattr(self._mod, "Sect", None)
        if item_cls is None or sect_cls is None:
            return

        by_name = {
            str(name).lower(): sect
            for name, sect in (getattr(resolver, "data", ()) or ())
        }

        def ensure_section(name):
            section = by_name.get(name)
            if section is None:
                section = sect_cls(name)
                by_name[name] = section
            return section

        for root_path, value in sorted(
            overrides.items(),
            key=lambda item: str(item[0]).lower(),
        ):
            original_text, current_text = value[0], value[1]
            line_sources = value[2] if len(value) > 2 else ()
            if original_text == current_text:
                continue

            changes, issues = self._resolved_changes(
                original_text,
                current_text,
                line_sources,
            )
            if any(issue.severity == "error" for issue in issues):
                _log(
                    log,
                    "Resolved overlay: %s — Пропущено (ошибки правки)"
                    % Path(root_path).name,
                )
                continue

            applied = 0
            for change in changes:
                kind = change.kind
                section = str(change.section or "").lower()
                key = change.key

                if kind == "delete_section":
                    by_name.pop(section, None)
                    applied += 1
                    continue

                if kind == "add_section":
                    snapshot = change.section_snapshot
                    if snapshot is None:
                        continue
                    target = ensure_section(section)
                    target.data = [
                        item_cls(key_name, key_value, str(root_path), 0, 0)
                        for key_name, key_value in snapshot.values
                    ]
                    applied += 1
                    continue

                if kind in ("add_parent", "remove_parent"):
                    continue

                if not key:
                    continue

                target = ensure_section(section)

                if kind == "delete_key":
                    target.data = [
                        item for item in target.data
                        if item.first != key
                    ]
                    applied += 1
                    continue

                if kind in (
                    "set_key",
                    "add_key",
                    "list_append",
                    "list_remove",
                ):
                    new_value = change.new_value
                    existing = None
                    for item in target.data:
                        if item.first == key:
                            existing = item
                            break
                    if existing is None:
                        target.data.append(
                            item_cls(
                                key,
                                new_value,
                                str(root_path),
                                0,
                                0,
                            )
                        )
                    else:
                        existing.second = new_value
                        existing.filename = str(root_path)
                    applied += 1

            _log(
                log,
                "Resolved overlay: %s — Выполнено (изменений: %d)"
                % (Path(root_path).name, applied),
            )

        resolver.data = sorted(
            by_name.items(),
            key=lambda entry: str(entry[0]).lower(),
        )

    def pass_only(self, root_path, overlay=None, log=None) -> int:
        """
        Делает проход резолвером и возвращает количество секций.
        """
        r = self._make_with_overlay(root_path, overlay, log=log)
        r.load()
        self._apply_resolved_overrides(
            r,
            getattr(overlay, "resolved_edits", None),
            log=log,
        )

        return len(getattr(r, "data", []))

    def assemble_text(self, root_path, source_map=None, overlay=None,
                      log=None) -> str:
        """
        Собирает итоговый текст через внешний резолвер.
        """
        r = self._make_with_overlay(root_path, overlay, log=log)
        r.load()
        self._apply_resolved_overrides(
            r,
            getattr(overlay, "resolved_edits", None),
            log=log,
        )

        sm = source_map or {}

        lines = [
            "; Assembled root: %s" % root_path,
            "; Resolver module: %s" % self.resolver_script,
            "",
        ]

        for sec_name, sect in r.data:
            sec_src = ""

            for it in sect.data:
                if getattr(it, "filename", ""):
                    sec_src = pretty_source(it.filename, sm)
                    break

            if sec_src:
                lines.append("[%s] ; <= %s" % (sec_name, sec_src))
            else:
                lines.append("[%s]" % sec_name)

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

    def assemble_resolved(self, root_path, source_map=None, abs_map=None,
                          overlay=None, log=None):
        """Чистый резолв + карта владельцев построчно.

        Returns:
            (text, line_sources): текст и список той же длины, что и число
            строк в ``text``. Элемент ``line_sources[i]``:
              - :class:`core.models.LineSource` — владелец строки;
              - ``None`` — владельца нет (преамбула, пустые строки,
                продолжения многострочных значений).
        """
        r = self._make_with_overlay(root_path, overlay, log=log)
        r.load()
        self._apply_resolved_overrides(
            r,
            getattr(overlay, "resolved_edits", None),
            log=log,
        )

        sm = source_map or {}
        abs_map = abs_map or {}

        # Resolver4 хранит в Item.filename stem без расширения в lower-case —
        # ключ для source_map и abs_map совпадает с ним.
        root_basename_no_ext = Path(root_path).stem.lower()

        # Один и тот же источник встречается в тысячах строк (system.ltx —
        # больше миллиона). Держим по одному LineSource на filename: это
        # резко уменьшает память и позволяет pickle сохранять ссылки, а не
        # миллион одинаковых объектов (иначе чтение кэша занимает секунды).
        src_cache = {}

        def src_of(fn):
            if not fn:
                return None

            cached = src_cache.get(fn)
            if cached is not None:
                return cached

            if fn == root_basename_no_ext:
                source = LineSource(
                    kind="original",
                    display="",
                    path=Path(root_path),
                    open_raw=False,
                )
            else:
                pretty = pretty_source(fn, sm)
                full = abs_map.get(fn)
                source = LineSource(
                    kind="file",
                    display=pretty,
                    path=Path(full) if full is not None else None,
                    open_raw=False,
                )

            src_cache[fn] = source
            return source

        lines = []
        line_sources = []

        for h in (
            "; Assembled root: %s" % root_path,
            "; Resolver module: %s" % self.resolver_script,
            "",
        ):
            lines.append(h)
            line_sources.append(None)

        for sec_name, sect in r.data:
            sec_src = None
            for it in sect.data:
                fn = getattr(it, "filename", "")
                if fn:
                    sec_src = src_of(fn)
                    break

            lines.append("[%s]" % sec_name)
            line_sources.append(sec_src)

            for it in sect.data:
                key = str(getattr(it, "first", "") or "")
                val = getattr(it, "second", "")

                if val is None:
                    val_s = ""
                elif hasattr(val, "name") and type(val).__name__ == "DLTXToken":
                    val_s = "<%s>" % val.name
                else:
                    val_s = str(val)

                src = src_of(getattr(it, "filename", ""))

                if "\n" in val_s or "\r" in val_s:
                    sub_lines = val_s.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                    lines.append("%s = %s" % (key, sub_lines[0]))
                    line_sources.append(src)
                    for sub in sub_lines[1:]:
                        lines.append(sub)
                        line_sources.append(None)
                elif val_s == "":
                    lines.append("%s =" % key)
                    line_sources.append(src)
                else:
                    lines.append("%s = %s" % (key, val_s))
                    line_sources.append(src)

            lines.append("")
            line_sources.append(None)

        return "\n".join(lines), line_sources

    def resolve_with_virtual_patch(
        self,
        root_path,
        patch_filename: str,
        patch_text: str,
    ):
        """Разрешить корень с виртуально подмешанным патчем.

        Патч не записывается на диск: экземпляру резолвера подменяются
        чтение файла, список файлов и загрузка include. Это позволяет
        проверять патч тем же ``Resolver4``, который видит игра.

        Returns:
            dict: ``{section: {key: value}}`` для итогового состояния.
        """
        import os

        from core.tools.fs import pattern_match

        resolver = self._make(root_path)
        root_path = Path(root_path)
        patch_path = root_path.parent / patch_filename

        patch_norm = os.path.normcase(os.path.abspath(str(patch_path)))
        folder_norm = os.path.normcase(os.path.abspath(str(root_path.parent)))

        original_read_lines = resolver._read_lines
        original_load_file = resolver._load_file
        original_file_list = resolver._file_list

        def read_lines(path):
            current = os.path.normcase(os.path.abspath(str(path)))
            if current == patch_norm:
                return str(patch_text).replace("\r\n", "\n").split("\n")
            return original_read_lines(path)

        def load_file(fn, inc_path, name, current_file_name, depth):
            current = os.path.normcase(os.path.abspath(str(fn)))
            if current == patch_norm:
                previous = current_file_name[0]
                current_file_name[0] = name
                resolver._ltx_load(
                    fn,
                    inc_path,
                    False,
                    current_file_name,
                    depth,
                )
                current_file_name[0] = previous
                return
            return original_load_file(
                fn,
                inc_path,
                name,
                current_file_name,
                depth,
            )

        def file_list(folder, mask):
            result = list(original_file_list(folder, mask))
            current = os.path.normcase(os.path.abspath(str(folder)))
            if current == folder_norm and pattern_match(
                patch_filename,
                mask,
            ):
                if patch_filename not in result:
                    result.append(patch_filename)
            return sorted(set(result), key=lambda value: value.lower())

        resolver._read_lines = read_lines
        resolver._load_file = load_file
        resolver._file_list = file_list

        resolver.load()

        return self._to_section_dict(resolver)

    @staticmethod
    def _to_section_dict(resolver):
        return {
            section_name: {
                item.first: item.second
                for item in section.data
                if getattr(item, "first", None)
            }
            for section_name, section in resolver.data
        }

    def resolve_sections(self, root_path):
        """Разрешить корень и вернуть итоговые секции без патча."""
        resolver = self._make(root_path)
        resolver.load()
        return self._to_section_dict(resolver)

    def assemble_to_file(self, root_path, out_dir, source_map=None) -> Path:
        """
        Собирает текст и записывает его во временный/служебный файл.
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        rp = Path(root_path)

        out = out_dir / (
            "%s__%s__assembled.ltx"
            % (
                safe_filename_component(rp.parent.name),
                safe_filename_component(rp.stem),
            )
        )

        out.write_text(
            self.assemble_text(rp, source_map),
            encoding="utf-8",
        )

        return out


# Совместимость со старым именем.
Assembler = LtxResolverAdapter
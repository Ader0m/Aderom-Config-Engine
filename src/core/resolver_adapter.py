# -*- coding: utf-8 -*-
"""LtxResolverAdapter — адаптер к внешнему резолверу Resolver4.py."""

from pathlib import Path

from .fs import (
    find_resolver_script,
    load_resolver_module,
    pretty_source,
)


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

    def pass_only(self, root_path) -> int:
        """
        Делает проход резолвером и возвращает количество секций.
        """
        r = self._make(root_path)
        r.load()

        return len(getattr(r, "data", []))

    def assemble_text(self, root_path, source_map=None) -> str:
        """
        Собирает итоговый текст через внешний резолвер.
        """
        r = self._make(root_path)
        r.load()

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

    def assemble_to_file(self, root_path, out_dir, source_map=None) -> Path:
        """
        Собирает текст и записывает его во временный/служебный файл.
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        rp = Path(root_path)

        def safe(s):
            return "".join(
                c if c.isalnum() or c in "_-" else "_"
                for c in s
            ).lower()

        out = out_dir / (
            "%s__%s__assembled.ltx"
            % (safe(rp.parent.name), safe(rp.stem))
        )

        out.write_text(
            self.assemble_text(rp, source_map),
            encoding="utf-8",
        )

        return out


# Совместимость со старым именем.
Assembler = LtxResolverAdapter
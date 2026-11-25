# -*- coding: utf-8 -*-
"""Чтение и валидация ``module.json``.

Манифест читается до импорта кода модуля (ADR-31). Class-manifest не
поддерживается: отсутствие ``module.json`` — причина ``SKIPPED``, а не
повод искать манифест в классе модуля.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sdk.kernel.module import (
    MODULE_MANIFEST_VERSION,
    CapabilityDeclaration,
    CapabilityRequirement,
    ModuleManifest,
    ModuleRequirement,
)

MANIFEST_NAME = "module.json"

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ENTRYPOINT_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:MODULE$"
)
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_CAPABILITY_RE = re.compile(r"^[a-z0-9_.]+$")

_TRUST_VALUES = ("core", "trusted", "untrusted")

_TOP_LEVEL_KEYS = frozenset({
    "manifest_version",
    "id",
    "name",
    "version",
    "description",
    "author",
    "entrypoint",
    "sdk",
    "requires",
    "optional_requires",
    "needs",
    "optional_needs",
    "provides",
    "trust",
    "enabled_by_default",
    "platforms",
})
_REQUIRED_TOP_LEVEL_KEYS = (
    "manifest_version",
    "id",
    "name",
    "version",
    "entrypoint",
    "sdk",
    "trust",
    "enabled_by_default",
)
_SDK_KEYS = frozenset({"min", "max"})
_REQUIREMENT_KEYS = frozenset({"id", "min_version"})
_CAPABILITY_REQUIREMENT_KEYS = frozenset({"name", "min_version"})
_CAPABILITY_DECLARATION_KEYS = frozenset({"name", "version"})


class ModuleManifestError(Exception):
    """Манифест отсутствует или не проходит валидацию."""

    def __init__(self, kind, detail=""):
        super().__init__(detail or kind)
        self.kind = str(kind)
        self.detail = str(detail or "")


def read_module_manifest(package_dir, warn=None) -> ModuleManifest:
    """Прочитать и проверить ``module.json`` каталога модуля.

    ``package_dir`` — каталог ``modules/<id>``; его имя обязано совпадать
    с ``id`` манифеста. ``warn`` вызывается для неизвестных полей.
    """
    package_dir = Path(package_dir)
    path = package_dir / MANIFEST_NAME
    if not path.is_file():
        raise ModuleManifestError(
            "manifest_missing",
            "нет файла %s" % MANIFEST_NAME,
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModuleManifestError(
            "manifest_invalid",
            "не удалось прочитать %s: %s" % (MANIFEST_NAME, exc),
        )

    if not isinstance(data, dict):
        raise ModuleManifestError(
            "manifest_invalid",
            "%s должен содержать JSON-объект" % MANIFEST_NAME,
        )

    warn = warn or (lambda message: None)
    _warn_unknown(data, _TOP_LEVEL_KEYS, MANIFEST_NAME, warn)

    for key in _REQUIRED_TOP_LEVEL_KEYS:
        if key not in data:
            raise ModuleManifestError(
                "manifest_invalid",
                "отсутствует обязательное поле %r" % key,
            )

    manifest_version = data["manifest_version"]
    if isinstance(manifest_version, bool) or not isinstance(
        manifest_version,
        int,
    ):
        raise ModuleManifestError(
            "manifest_invalid",
            "manifest_version должен быть целым",
        )
    if manifest_version > MODULE_MANIFEST_VERSION:
        raise ModuleManifestError(
            "unsupported_manifest_version",
            "manifest_version=%s, поддерживается только %s"
            % (manifest_version, MODULE_MANIFEST_VERSION),
        )
    if manifest_version != MODULE_MANIFEST_VERSION:
        raise ModuleManifestError(
            "manifest_invalid",
            "manifest_version=%s, поддерживается только %s"
            % (manifest_version, MODULE_MANIFEST_VERSION),
        )

    module_id = _require_text(data, "id", MANIFEST_NAME)
    if not _ID_RE.match(module_id):
        raise ModuleManifestError(
            "manifest_invalid",
            "id %r должен соответствовать ^[a-z][a-z0-9_]*$" % module_id,
        )
    if module_id != package_dir.name:
        raise ModuleManifestError(
            "id_mismatch",
            "id %r не совпадает с каталогом %r"
            % (module_id, package_dir.name),
        )

    name = _require_text(data, "name", MANIFEST_NAME)
    version = _require_semver(data, "version", MANIFEST_NAME)
    entrypoint = _require_text(data, "entrypoint", MANIFEST_NAME)
    if not _ENTRYPOINT_RE.match(entrypoint):
        raise ModuleManifestError(
            "manifest_invalid",
            "entrypoint должен иметь вид '<подмодуль>:MODULE'",
        )

    sdk = data["sdk"]
    if not isinstance(sdk, dict):
        raise ModuleManifestError(
            "manifest_invalid",
            "поле sdk должно быть объектом {min, max}",
        )
    _warn_unknown(sdk, _SDK_KEYS, "sdk", warn)
    if "min" not in sdk:
        raise ModuleManifestError(
            "manifest_invalid",
            "поле sdk.min обязательно",
        )
    sdk_min = _require_semver(sdk, "min", "sdk")
    sdk_max = ""
    if "max" in sdk:
        sdk_max = _require_semver(sdk, "max", "sdk")
        if _version_key(sdk_max) < _version_key(sdk_min):
            raise ModuleManifestError(
                "manifest_invalid",
                "sdk.max не может быть меньше sdk.min",
            )

    description = _optional_text(data, "description", MANIFEST_NAME)
    author = _optional_text(data, "author", MANIFEST_NAME)

    requires = _read_requirements(data, "requires", warn)
    optional_requires = _read_requirements(
        data,
        "optional_requires",
        warn,
    )
    needs = _read_capability_requirements(data, "needs", warn)
    optional_needs = _read_capability_requirements(
        data,
        "optional_needs",
        warn,
    )
    provides = _read_capability_declarations(data, "provides", warn)

    trust = _require_text(data, "trust", MANIFEST_NAME)
    if trust not in _TRUST_VALUES:
        raise ModuleManifestError(
            "manifest_invalid",
            "trust должен быть одним из %s" % (", ".join(_TRUST_VALUES)),
        )

    enabled_by_default = data["enabled_by_default"]
    if not isinstance(enabled_by_default, bool):
        raise ModuleManifestError(
            "manifest_invalid",
            "enabled_by_default должен быть boolean",
        )

    platforms = data.get("platforms", ["windows"])
    if not isinstance(platforms, list) or not all(
        isinstance(item, str) and item
        for item in platforms
    ):
        raise ModuleManifestError(
            "manifest_invalid",
            "platforms должен быть массивом непустых строк",
        )

    return ModuleManifest(
        id=module_id,
        name=name,
        version=version,
        manifest_version=manifest_version,
        entrypoint=entrypoint,
        sdk_min=sdk_min,
        sdk_max=sdk_max,
        description=description,
        author=author,
        requires=requires,
        optional_requires=optional_requires,
        provides=provides,
        needs=needs,
        optional_needs=optional_needs,
        trust=trust,
        enabled_by_default=enabled_by_default,
        platforms=tuple(platforms),
    )


def _warn_unknown(data, known, prefix, warn):
    unknown = sorted(set(data) - set(known))
    if unknown:
        warn(
            "%s: неизвестные поля проигнорированы: %s"
            % (prefix, ", ".join(unknown))
        )


def _require_text(data, key, prefix):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ModuleManifestError(
            "manifest_invalid",
            "поле %s.%s должно быть непустой строкой" % (prefix, key),
        )
    return value.strip()


def _optional_text(data, key, prefix):
    if key not in data:
        return ""
    value = data[key]
    if not isinstance(value, str):
        raise ModuleManifestError(
            "manifest_invalid",
            "поле %s.%s должно быть строкой" % (prefix, key),
        )
    return value.strip()


def _require_semver(data, key, prefix):
    value = data.get(key)
    if not isinstance(value, str) or not _SEMVER_RE.match(value):
        raise ModuleManifestError(
            "manifest_invalid",
            "поле %s.%s должно быть semver (X.Y.Z)" % (prefix, key),
        )
    return value


def _version_key(value):
    return tuple(int(part) for part in value.split("."))


def _read_requirements(data, key, warn):
    values = data.get(key, [])
    if not isinstance(values, list):
        raise ModuleManifestError(
            "manifest_invalid",
            "поле %s должно быть массивом" % key,
        )
    result = []
    seen = set()
    for index, value in enumerate(values):
        prefix = "%s[%d]" % (key, index)
        if not isinstance(value, dict):
            raise ModuleManifestError(
                "manifest_invalid",
                "элемент %s должен быть объектом" % prefix,
            )
        _warn_unknown(value, _REQUIREMENT_KEYS, prefix, warn)
        if "id" not in value:
            raise ModuleManifestError(
                "manifest_invalid",
                "элемент %s: поле id обязательно" % prefix,
            )
        module_id = _require_text(value, "id", prefix)
        if not _ID_RE.match(module_id):
            raise ModuleManifestError(
                "manifest_invalid",
                "элемент %s: некорректный id %r" % (prefix, module_id),
            )
        if module_id in seen:
            raise ModuleManifestError(
                "manifest_invalid",
                "элемент %s: повторный id %r" % (prefix, module_id),
            )
        seen.add(module_id)
        min_version = ""
        if "min_version" in value:
            min_version = _require_semver(value, "min_version", prefix)
        result.append(
            ModuleRequirement(id=module_id, min_version=min_version)
        )
    return tuple(result)


def _read_capability_requirements(data, key, warn):
    values = data.get(key, [])
    if not isinstance(values, list):
        raise ModuleManifestError(
            "manifest_invalid",
            "поле %s должно быть массивом" % key,
        )
    result = []
    seen = set()
    for index, value in enumerate(values):
        prefix = "%s[%d]" % (key, index)
        if not isinstance(value, dict):
            raise ModuleManifestError(
                "manifest_invalid",
                "элемент %s должен быть объектом" % prefix,
            )
        _warn_unknown(value, _CAPABILITY_REQUIREMENT_KEYS, prefix, warn)
        name = _require_capability_name(value, prefix)
        if name in seen:
            raise ModuleManifestError(
                "manifest_invalid",
                "элемент %s: повторная capability %r" % (prefix, name),
            )
        seen.add(name)
        if "min_version" not in value:
            raise ModuleManifestError(
                "manifest_invalid",
                "элемент %s: min_version обязателен" % prefix,
            )
        min_version = _require_semver(value, "min_version", prefix)
        result.append(
            CapabilityRequirement(name=name, min_version=min_version)
        )
    return tuple(result)


def _read_capability_declarations(data, key, warn):
    values = data.get(key, [])
    if not isinstance(values, list):
        raise ModuleManifestError(
            "manifest_invalid",
            "поле %s должно быть массивом" % key,
        )
    result = []
    seen = set()
    for index, value in enumerate(values):
        prefix = "%s[%d]" % (key, index)
        if not isinstance(value, dict):
            raise ModuleManifestError(
                "manifest_invalid",
                "элемент %s должен быть объектом" % prefix,
            )
        _warn_unknown(value, _CAPABILITY_DECLARATION_KEYS, prefix, warn)
        name = _require_capability_name(value, prefix)
        if name in seen:
            raise ModuleManifestError(
                "manifest_invalid",
                "элемент %s: повторная capability %r" % (prefix, name),
            )
        seen.add(name)
        if "version" not in value:
            raise ModuleManifestError(
                "manifest_invalid",
                "элемент %s: version обязательна" % prefix,
            )
        version = _require_semver(value, "version", prefix)
        result.append(CapabilityDeclaration(name=name, version=version))
    return tuple(result)


def _require_capability_name(value, prefix):
    if "name" not in value:
        raise ModuleManifestError(
            "manifest_invalid",
            "элемент %s: поле name обязательно" % prefix,
        )
    name = _require_text(value, "name", prefix)
    if not _CAPABILITY_RE.match(name):
        raise ModuleManifestError(
            "manifest_invalid",
            "элемент %s: некорректное имя capability %r" % (prefix, name),
        )
    return name

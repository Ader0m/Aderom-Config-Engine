# -*- coding: utf-8 -*-
"""ProjectApiAdapter: настройки, asset-типы и читатели проекта."""

from __future__ import annotations

from pathlib import Path

from sdk.features.documents.models import FileView
from sdk.features.projects.models import (
    AssetDraftInfo,
    ProjectAssetType,
    ProjectSetting,
    ProjectSettingChoice,
)
from sdk.kernel.registrations import RegistrationHandle

from .common import _SDK_API_MARKER


class ProjectApiAdapter:
    """Реализация :class:`sdk.features.projects.ProjectApi`."""

    _sdk_api_kind = "projects"

    def __init__(self, platform):
        self._platform = platform
        self._owner = ""
        self._scope = None

    def bind_owner(self, owner, scope=None):
        self._owner = str(owner or "")
        self._scope = scope

    @classmethod
    def wrap(cls, platform):
        if getattr(platform, _SDK_API_MARKER, None) == cls._sdk_api_kind:
            return platform
        return cls(platform)

    def _track(self, handle):
        if self._scope is not None:
            self._scope.track(handle)
        return handle

    def register_project_asset_type(self, asset_type):
        ok = self._platform.register_project_asset_type(
            asset_type,
            owner=self._owner,
        )
        if ok:
            key = str(getattr(asset_type, "key", "") or "")
            self._track(RegistrationHandle(
                lambda: self._platform.unregister_project_asset_type(
                    key,
                    owner=self._owner,
                ),
                self._owner,
            ))
        return bool(ok)

    def unregister_project_asset_type(self, key):
        return self._platform.unregister_project_asset_type(
            key,
            owner=self._owner,
        )

    def project_asset_types(self):
        result = []
        for item in self._platform.project_asset_types():
            result.append(
                ProjectAssetType(
                    key=str(getattr(item, "key", "") or ""),
                    label=str(getattr(item, "label", "") or ""),
                    extensions=tuple(
                        str(value) for value in getattr(
                            item,
                            "extensions",
                            (),
                        )
                    ),
                    vfs_root=str(getattr(item, "vfs_root", "") or ""),
                )
            )
        return tuple(result)

    def register_project_setting(self, setting):
        ok = bool(
            self._platform.register_project_setting(setting, owner=self._owner)
        )
        if ok:
            key = str(getattr(setting, "key", "") or "")
            self._track(RegistrationHandle(
                lambda: self._platform.unregister_project_setting(
                    key,
                    owner=self._owner,
                ),
                self._owner,
            ))
        return ok

    def unregister_project_setting(self, key):
        return bool(
            self._platform.unregister_project_setting(key, owner=self._owner)
        )

    def project_settings(self):
        result = []
        for item in self._platform.project_setting_definitions():
            result.append(
                ProjectSetting(
                    key=str(getattr(item, "key", "") or ""),
                    label=str(getattr(item, "label", "") or ""),
                    kind=str(getattr(item, "kind", "") or "choice"),
                    default=getattr(item, "default", None),
                    choices=tuple(
                        ProjectSettingChoice(
                            value=str(
                                getattr(choice, "value", choice) or ""
                            ),
                            label=str(
                                getattr(choice, "label", "")
                                or getattr(choice, "value", choice)
                                or ""
                            ),
                        )
                        for choice in getattr(item, "choices", ()) or ()
                    ),
                    description=str(
                        getattr(item, "description", "") or ""
                    ),
                )
            )
        return tuple(result)

    def project_setting(self, key, default=None):
        return self._platform.project_setting(key, default)

    def set_project_setting(self, key, value):
        return bool(self._platform.set_project_setting(key, value))

    def register_file_reader(self, reader):
        subscription = self._platform.register_file_reader(reader)
        return self._track(subscription)

    def read_file_view(self, path, data=None):
        view = self._platform.read_file_view(path, data)
        if view is None:
            return None
        return FileView(
            text=str(getattr(view, "text", "") or ""),
            label=str(getattr(view, "label", "") or ""),
            details=tuple(
                str(value) for value in getattr(view, "details", ())
            ),
        )

    def read_project_asset(self, path):
        return self._platform.read_project_asset(path)

    def update_project_asset(self, path, data):
        return self._platform.update_project_asset(path, data)

    def save_project_asset(self, path, data=None):
        return self._platform.save_project_asset(path, data)

    def project_asset_drafts(self):
        result = []
        for draft in self._platform.project_asset_drafts():
            result.append(
                AssetDraftInfo(
                    path=Path(draft.path),
                    is_new=bool(getattr(draft, "is_new", False)),
                    changed=bool(getattr(draft, "changed", False)),
                    dirty=bool(getattr(draft, "dirty", False)),
                    size=int(getattr(draft, "size", 0) or 0),
                )
            )
        return tuple(result)

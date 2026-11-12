# -*- coding: utf-8 -*-
"""Читатель .ogg для проекта: показывает комментарий движка."""

from __future__ import annotations

from pathlib import Path

from sdk import FileReader, FileView

from .ogg import (
    OggError,
    format_editor_text,
    parse_xray_comment,
    read_comments,
    read_stream_info,
)
from .sound_types import sound_type_display


class OggCommentReader(FileReader):
    """Финальный вид .ogg — его Vorbis-комментарий."""

    priority = 100

    def can_read(self, path, data=None):
        try:
            return Path(path).suffix.lower() == ".ogg"
        except (TypeError, ValueError):
            return False

    def read(self, path, data=None):
        if data is None:
            data = Path(path).read_bytes()

        payload = bytes(data)
        comments = read_comments(payload)
        details = []

        info = None
        try:
            info = read_stream_info(payload)
        except OggError as exc:
            details.append("Метаданные: %s" % exc)

        if info is not None:
            details.append(
                "Каналы: %d | Частота: %d Гц | Битрейт: %s"
                % (
                    info.channels,
                    info.sample_rate,
                    (
                        "%.0f kbps" % info.bitrate_kbps
                        if info.bitrate_kbps
                        else "не указан"
                    ),
                )
            )
            details.append(
                "Serial: 0x%08X | Vendor: %s"
                % (info.serial, info.vendor or "не указан")
            )
            details.append(
                "Длительность: %s | CRC: %s"
                % (
                    _format_duration(info.duration),
                    "OK" if info.crc_ok else "ошибка",
                )
            )

        params = parse_xray_comment(comments.engine_comment)
        if params is not None:
            details.append(
                "X-Ray v%d: min %.2f / max %.2f / volume %.2f | %s"
                % (
                    params.version,
                    params.min_dist,
                    params.max_dist,
                    params.effective_base_volume(),
                    sound_type_display(params.game_type),
                )
            )
        elif comments.engine_comment:
            details.append("Текстовый Vorbis comment")
        else:
            details.append("Комментарий отсутствует")

        extra = max(0, len(comments.comments) - 1)
        if extra:
            details.append("Дополнительных комментариев: %d" % extra)

        return FileView(
            text=format_editor_text(comments),
            label="Комментарии Ogg Vorbis",
            details=tuple(details),
        )


def _format_duration(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds or 0) * 1000)))
    total = total_ms // 1000
    return "%d:%02d.%03d" % (
        total // 60,
        total % 60,
        total_ms % 1000,
    )

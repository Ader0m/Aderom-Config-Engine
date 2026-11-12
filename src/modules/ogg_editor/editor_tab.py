# -*- coding: utf-8 -*-
"""Вкладка редактора комментариев .ogg с прослушиванием звука."""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import wave
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QShortcut,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from sdk.events import Events

from .ogg import (
    CommentFormatError,
    OggCrcError,
    OggError,
    describe_comment,
    extract_game_type,
    format_editor_text,
    parse_editor_text,
    parse_xray_comment,
    read_comments,
    read_stream_info,
    replace_game_type,
    rewrite_engine_comment,
    validate_crc,
)
from .sound_types_ui import SoundTypeField

try:
    from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
except Exception:  # pragma: no cover - QtMultimedia может отсутствовать
    QMediaContent = None
    QMediaPlayer = None

try:
    import soundfile as _soundfile
except Exception:  # pragma: no cover - необязательное расширение
    _soundfile = None


class OggEditorTab(QWidget):
    """Текст комментария + плеер. Сохраняет байты в черновик проекта."""

    status_changed = pyqtSignal(str)

    def __init__(self, ctx, tab_id, path, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.tab_id = tab_id
        self.path = Path(path)

        self._loading = False
        self._readonly = not bool(ctx.ui.is_project_active())
        self._data = b""
        self._saved_data = b""
        self._comments = None
        self._stream_info = None
        self._binary_mode = True
        self._saved_text = ""
        self._crc_ok = True
        self._crc_error = ""
        self._syncing_type = False
        self._play_temp_dir: Optional[str] = None
        self._player = None
        self._disposed = False

        self._load()
        self._build_ui()
        self._init_player()
        self._init_shortcuts()
        self._apply_editable()

        sub = ctx.events.subscribe(
            Events.PROJECT_SAVED,
            self._on_project_saved,
        )
        self._project_saved_sub = sub

    # ------------------------------------------------------------ данные
    def _load(self):
        data = None
        try:
            data = self.ctx.ui.read_project_asset(self.path)
        except Exception:
            data = None

        if data is None:
            try:
                data = self.path.read_bytes()
            except OSError as exc:
                self._load_error = "Не удалось прочитать файл: %s" % exc
                self._data = b""
                return

        self._data = bytes(data)
        self._saved_data = self._data

        self._crc_ok = True
        self._crc_error = ""
        try:
            validate_crc(self._data)
        except OggCrcError as exc:
            self._crc_ok = False
            self._crc_error = str(exc)
        except OggError:
            # Структурную ошибку покажет read_comments ниже.
            pass

        try:
            self._comments = read_comments(self._data)
            self._load_error = ""
            self._binary_mode = (
                not self._comments.engine_comment
                or parse_xray_comment(self._comments.engine_comment) is not None
            )
        except (OggError, Exception) as exc:
            self._comments = None
            self._load_error = "Ошибка Ogg/Vorbis: %s" % exc

        try:
            self._stream_info = read_stream_info(self._data)
        except (OggError, Exception):
            self._stream_info = None

    def _initial_text(self) -> str:
        if self._comments is not None:
            return format_editor_text(self._comments)
        return self._load_error

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.path_label = QLabel(str(self.path))
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setStyleSheet(
            "padding: 4px; background-color: palette(window);"
        )
        layout.addWidget(self.path_label)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.summary_label)

        self.metadata_label = QLabel("")
        self.metadata_label.setWordWrap(True)
        layout.addWidget(self.metadata_label)

        type_row = QHBoxLayout()
        type_row.setSpacing(6)
        type_row.addWidget(QLabel("Тип звука:"))
        self.sound_type_combo = SoundTypeField(allow_none=True)
        self.sound_type_combo.setMinimumWidth(360)
        self.sound_type_combo.valueChanged.connect(
            self._on_sound_type_changed
        )
        type_row.addWidget(self.sound_type_combo, 1)
        layout.addLayout(type_row)

        controls = QHBoxLayout()
        controls.setSpacing(6)

        green = self.ctx.ui.theme_color("accent_green", "#3FA34D")
        self.play_button = QPushButton("▶  Слушать")
        self.play_button.setToolTip("Воспроизвести звук с начала")
        self.play_button.setStyleSheet(
            "QPushButton {"
            " background-color: %s;"
            " color: white;"
            " font-weight: 600;"
            " padding: 5px 14px;"
            " border: none;"
            " border-radius: 4px;"
            "}"
            "QPushButton:disabled { background-color: palette(mid); }"
            % green
        )
        self.play_button.clicked.connect(self._play)
        controls.addWidget(self.play_button)

        self.pause_button = QPushButton("❚❚  Пауза")
        self.pause_button.setToolTip("Приостановить или продолжить")
        self.pause_button.clicked.connect(self._toggle_pause)
        self.pause_button.hide()
        controls.addWidget(self.pause_button)

        self.stop_button = QPushButton("■  Стоп")
        self.stop_button.setToolTip("Остановить и вернуться в начало")
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.hide()
        controls.addWidget(self.stop_button)

        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self._seek)
        self.position_slider.hide()
        controls.addWidget(self.position_slider, 1)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setMinimumWidth(90)
        controls.addWidget(self.time_label)

        self.volume_label = QLabel("Громкость:")
        controls.addWidget(self.volume_label)
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setMaximumWidth(110)
        self.volume_slider.valueChanged.connect(self._set_volume)
        controls.addWidget(self.volume_slider)

        layout.addLayout(controls)

        hint = QLabel(
            "Текст — это Vorbis-комментарий движка. Поля X-Ray "
            "(version, min_dist, max_dist, base_volume, game_type, "
            "max_ai_dist) записываются в бинарную структуру; строки без "
            "полей — пояснения и в бинарный комментарий не попадают. "
            "Обычный текстовый комментарий сохраняется как UTF-8. "
            "Аудиоданные не перекодируются."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)

        self.editor = QPlainTextEdit()
        fixed = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self.editor.setFont(fixed)
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._loading = True
        try:
            self.editor.setPlainText(self._initial_text())
        finally:
            self._loading = False
        self._saved_text = self.editor.toPlainText()
        self.editor.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.editor, 1)

        bottom = QHBoxLayout()
        self.save_button = QPushButton("Сохранить в проект")
        self.save_button.clicked.connect(self.save)
        bottom.addWidget(self.save_button)
        bottom.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: palette(mid);")
        bottom.addWidget(self.status_label)
        layout.addLayout(bottom)

        self._refresh_summary()
        self._sync_game_type_from_text()
        self._update_status()

    def _init_shortcuts(self):
        QShortcut(
            "Ctrl+S",
            self,
            context=Qt.WidgetWithChildrenShortcut,
            activated=self.save,
        )

    def _update_status(self, message: str = ""):
        if message:
            self.status_label.setText(message)
            self.status_changed.emit(message)
            return

        if self._load_error:
            self.status_label.setText(self._load_error)
        elif not self._crc_ok:
            self.status_label.setText(
                "Запись запрещена: %s" % self._crc_error
            )
        elif not self.ctx.ui.is_project_active():
            self.status_label.setText(
                "Режим чтения: создайте или загрузите проект"
            )
        elif self.editor.toPlainText() != self._saved_text:
            self.status_label.setText("Есть несохранённые изменения")
        else:
            self.status_label.setText("Сохранено в проекте")

    # ------------------------------------------------------------ плеер
    def _init_player(self):
        if QMediaPlayer is None or QMediaContent is None:
            self.play_button.setEnabled(False)
            self.play_button.setToolTip("QtMultimedia недоступен")
            return

        self._player = QMediaPlayer(self)
        self._player.setVolume(100)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.stateChanged.connect(self._on_player_state)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        try:
            self._player.error.connect(self._on_player_error)
        except Exception:
            pass

    def _media_path(self) -> Optional[Path]:
        """Файл для QMediaPlayer.

        Qt на Windows не всегда умеет Vorbis (нет системного кодека).
        Если доступен ``soundfile`` (libsndfile), декодируем текущий
        контейнер в WAV; иначе отдаём Qt сам .ogg.
        """
        decoded = self._decoded_wav_path()
        if decoded is not None:
            return decoded

        try:
            current = self.path.read_bytes()
        except OSError:
            current = None

        if current is not None and current == self._data:
            return self.path

        if self._play_temp_dir is None:
            self._play_temp_dir = tempfile.mkdtemp(prefix="ace_ogg_")

        target = Path(self._play_temp_dir) / ("preview_" + self.path.name)
        try:
            target.write_bytes(self._data)
            return target
        except OSError as exc:
            self._update_status("Не удалось подготовить звук: %s" % exc)
            return None

    def _decoded_wav_path(self) -> Optional[Path]:
        if _soundfile is None:
            return None

        if self._play_temp_dir is None:
            self._play_temp_dir = tempfile.mkdtemp(prefix="ace_ogg_")

        digest = hashlib.sha1(self._data).hexdigest()[:16]
        target = Path(self._play_temp_dir) / ("preview_%s.wav" % digest)
        if target.is_file():
            return target

        try:
            with _soundfile.SoundFile(io.BytesIO(self._data)) as source:
                with wave.open(str(target), "wb") as handle:
                    handle.setnchannels(int(source.channels))
                    handle.setsampwidth(2)
                    handle.setframerate(int(source.samplerate))
                    while True:
                        block = source.read(
                            65536,
                            dtype="int16",
                            always_2d=True,
                        )
                        if len(block) == 0:
                            break
                        handle.writeframes(block.tobytes())
            return target
        except Exception as exc:
            self.ctx.log("ogg_editor: декодирование для плеера: %s" % exc)
            try:
                target.unlink()
            except OSError:
                pass
            return None

    def _play(self):
        if self._player is None:
            return
        if self._comments is None:
            self._update_status("Файл не является корректным Ogg Vorbis")
            return

        media_path = self._media_path()
        if media_path is None:
            return

        self._player.stop()
        self._player.setMedia(
            QMediaContent(QUrl.fromLocalFile(str(media_path)))
        )
        self._player.setPosition(0)
        self._player.play()

    def _toggle_pause(self):
        if self._player is None:
            return
        if self._player.state() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _stop(self):
        if self._player is None:
            return
        self._player.stop()
        self._player.setPosition(0)
        self._set_transport_visible(False)
        self.position_slider.setValue(0)
        self.time_label.setText("0:00 / %s" % self._format_time(self._duration()))

    def _seek(self, position):
        if self._player is not None:
            self._player.setPosition(position)

    def _set_volume(self, value):
        if self._player is not None:
            self._player.setVolume(int(value))

    def _duration(self) -> int:
        return int(self._player.duration()) if self._player is not None else 0

    def _on_duration(self, duration):
        self.position_slider.setRange(0, max(0, int(duration)))
        self.time_label.setText(
            "%s / %s"
            % (
                self._format_time(self._player.position()),
                self._format_time(duration),
            )
        )

    def _on_position(self, position):
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(int(position))
        self.time_label.setText(
            "%s / %s"
            % (
                self._format_time(position),
                self._format_time(self._duration()),
            )
        )
        if int(position) > 0:
            self._set_transport_visible(True)

    def _on_player_state(self, state):
        if state == QMediaPlayer.PlayingState:
            self._set_transport_visible(True)
        elif state == QMediaPlayer.PausedState:
            self._set_transport_visible(True)
        elif state == QMediaPlayer.StoppedState:
            if self._player.position() <= 0:
                self._set_transport_visible(False)

    def _on_media_status(self, status):
        if QMediaPlayer is None:
            return
        if status == QMediaPlayer.InvalidMedia:
            self._update_status(
                "QtMultimedia не смог открыть .ogg. Проверьте кодек Vorbis "
                "в системе."
            )
        elif status == QMediaPlayer.EndOfMedia:
            self._stop()

    def _on_player_error(self, *_args):
        self._update_status(
            "Ошибка воспроизведения: %s" % self._player.errorString()
        )

    def _set_transport_visible(self, visible):
        self.pause_button.setVisible(bool(visible))
        self.stop_button.setVisible(bool(visible))
        self.position_slider.setVisible(bool(visible))

    @staticmethod
    def _format_time(value) -> str:
        try:
            total = max(0, int(value)) // 1000
        except (TypeError, ValueError):
            total = 0
        return "%d:%02d" % (total // 60, total % 60)

    # ------------------------------------------------------------ правка
    def _on_text_changed(self):
        if self._loading:
            return

        self._sync_game_type_from_text()

        if self._readonly or self._load_error:
            self._update_status()
            return

        if self.editor.toPlainText() == self._saved_text:
            if self.ctx.ui.update_project_asset(
                self.path,
                self._saved_data,
            ):
                self._data = self._saved_data
                self.ctx.ui.set_editor_tab_dirty(self.tab_id, False)
                self._update_status()
            return

        pending = self._pending_bytes()
        if pending is None:
            # Текст отличается от сохранённого, даже если его пока нельзя
            # превратить в корректный комментарий.
            self.ctx.ui.set_editor_tab_dirty(self.tab_id, True)
            return

        if self.ctx.ui.update_project_asset(self.path, pending):
            self._data = pending
            self._update_status()
            self.ctx.ui.set_editor_tab_dirty(self.tab_id, True)

    def _pending_bytes(self) -> Optional[bytes]:
        text = self.editor.toPlainText()
        try:
            comment = parse_editor_text(text, xray_only=self._binary_mode)
            return rewrite_engine_comment(self._data, comment)
        except (CommentFormatError, OggError) as exc:
            self._update_status("Проверьте текст: %s" % exc)
            return None
        except Exception as exc:
            self._update_status("Не удалось пересобрать Ogg: %s" % exc)
            return None

    def save(self):
        if not self._crc_ok:
            self._update_status(
                "Запись запрещена: %s" % self._crc_error
            )
            return
        if self._readonly:
            self._update_status(
                "Создайте или загрузите проект, чтобы сохранять изменения"
            )
            return

        data = self._pending_bytes()
        if data is None:
            return

        if not self.ctx.ui.save_project_asset(self.path, data):
            self._update_status("Не удалось сохранить в проект")
            return

        self._data = data
        self._saved_data = data
        self._saved_text = self.editor.toPlainText()
        self.ctx.ui.set_editor_tab_dirty(self.tab_id, False)
        self._refresh_summary()
        self._update_status("Сохранено в проекте")

    def _refresh_summary(self):
        try:
            comments = read_comments(self._data)
        except Exception:
            return
        text = describe_comment(comments)
        extra = max(0, len(comments.comments) - 1)
        if extra:
            text += " | дополнительных комментариев: %d (сохраняются)" % extra
        self.summary_label.setText(text)
        self._refresh_metadata()

    def _refresh_metadata(self):
        info = self._stream_info
        if info is None:
            try:
                info = read_stream_info(self._data)
                self._stream_info = info
            except Exception as exc:
                self.metadata_label.setText("Метаданные: %s" % exc)
                return

        bitrate = (
            "%.0f kbps" % info.bitrate_kbps
            if info.bitrate_kbps
            else "не указан"
        )
        self.metadata_label.setText(
            "Каналы: %d | Частота: %d Гц | Битрейт: %s | "
            "Serial: 0x%08X | Vendor: %s | Длительность: %s"
            % (
                info.channels,
                info.sample_rate,
                bitrate,
                info.serial,
                info.vendor or "не указан",
                self._format_time(int(info.duration * 1000)),
            )
        )
        self.metadata_label.setToolTip(
            "CRC: %s" % ("OK" if info.crc_ok else self._crc_error)
        )
        if not info.crc_ok:
            self.metadata_label.setStyleSheet(
                "color: #F14C4C; font-weight: 600;"
            )
        else:
            self.metadata_label.setStyleSheet("color: palette(mid);")

    def _sync_game_type_from_text(self):
        if self._syncing_type or not hasattr(self, "sound_type_combo"):
            return
        value = extract_game_type(self.editor.toPlainText())
        self._syncing_type = True
        try:
            self.sound_type_combo.set_value(value)
        finally:
            self._syncing_type = False

    def _on_sound_type_changed(self, value):
        if self._syncing_type or value is None:
            return

        text = self.editor.toPlainText()
        updated = replace_game_type(text, value)
        if updated == text:
            return

        position = self.editor.textCursor().position()
        self.editor.setPlainText(updated)
        cursor = self.editor.textCursor()
        cursor.setPosition(min(position, len(updated)))
        self.editor.setTextCursor(cursor)

    def apply_external_data(self, data):
        """Обновить вкладку после правки параметров из дерева."""
        self._data = bytes(data)
        self._saved_data = self._data
        self._crc_ok = True
        self._crc_error = ""

        try:
            self._comments = read_comments(self._data)
            self._load_error = ""
            self._binary_mode = (
                not self._comments.engine_comment
                or parse_xray_comment(self._comments.engine_comment) is not None
            )
        except (OggError, Exception) as exc:
            self._comments = None
            self._load_error = "Ошибка Ogg/Vorbis: %s" % exc

        try:
            self._stream_info = read_stream_info(self._data)
        except (OggError, Exception):
            self._stream_info = None

        self._loading = True
        try:
            self.editor.setPlainText(self._initial_text())
        finally:
            self._loading = False
        self._saved_text = self.editor.toPlainText()

        self._refresh_summary()
        self._sync_game_type_from_text()
        self.ctx.ui.set_editor_tab_dirty(self.tab_id, False)
        self._update_status("Обновлено из дерева")

    def mark_clean(self):
        self._saved_text = self.editor.toPlainText()
        self._saved_data = self._data
        self._update_status()

    def set_editable(self, editable):
        self._readonly = not (bool(editable) and self._crc_ok)
        self._apply_editable()

    def _apply_editable(self):
        if not self._crc_ok:
            self._readonly = True
        self.editor.setReadOnly(self._readonly)
        self.save_button.setEnabled(not self._readonly)
        self.sound_type_combo.setEnabled(not self._readonly)
        if self._readonly:
            if not self._crc_ok:
                self._update_status(
                    "Запись запрещена: %s" % self._crc_error
                )
            else:
                self._update_status(
                    "Режим чтения: создайте или загрузите проект"
                )
        else:
            self._update_status()

    def is_dirty(self) -> bool:
        return self.editor.toPlainText() != self._saved_text

    # ------------------------------------------------------------ события
    def _on_project_saved(self, event):
        try:
            if getattr(event, "kind", "") != "asset":
                return
            path = getattr(event, "path", None)
            if path is None:
                return
            if os.path.normcase(os.path.abspath(str(path))) != os.path.normcase(
                os.path.abspath(str(self.path))
            ):
                return
            self.ctx.ui.set_editor_tab_dirty(self.tab_id, False)
            self._update_status("Сохранено в проекте")
        except Exception:
            pass

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)

    def dispose(self):
        """Отписаться и освободить временный файл плеера."""
        if getattr(self, "_disposed", False):
            return
        self._disposed = True

        sub = getattr(self, "_project_saved_sub", None)
        if sub is not None and hasattr(sub, "unsubscribe"):
            try:
                sub.unsubscribe()
            except Exception:
                pass
        self._project_saved_sub = None
        self._cleanup_player()

    def _cleanup_player(self):
        if self._player is not None:
            try:
                self._player.stop()
                self._player.setMedia(QMediaContent())
            except Exception:
                pass
        if self._play_temp_dir:
            import shutil

            try:
                shutil.rmtree(self._play_temp_dir, ignore_errors=True)
            except Exception:
                pass
            self._play_temp_dir = None

# -*- coding: utf-8 -*-
"""OGG/Vorbis: чтение и замена комментария без перекодирования звука.

Движок X-Ray читает параметры звука из **первого** Vorbis-комментария
(``SoundRender_Source_loader.cpp``): это не текст, а бинарная структура
little-endian:

    u32 version
    float min_dist
    float max_dist
    float base_volume      (version >= 2)
    u32 game_type
    float max_ai_dist      (version == 3)

Модуль не трогает аудиопакеты. Он пересобирает только служебные страницы
заголовков Ogg (identification/comment/setup), а звуковые страницы
перенумеровывает при необходимости и пересчитывает CRC.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

CAPTURE = b"OggS"
VORBIS_ID = b"\x01vorbis"
VORBIS_COMMENT = b"\x03vorbis"
VORBIS_SETUP = b"\x05vorbis"

XRAY_VERSION = 3
DEFAULT_MIN_DIST = 1.0
DEFAULT_MAX_DIST = 300.0
DEFAULT_BASE_VOLUME = 1.0
DEFAULT_GAME_TYPE = 0
DEFAULT_MAX_AI_DIST = 300.0

# Размер бинарного комментария для каждой версии.
_VERSION_SIZE = {1: 16, 2: 20, 3: 24}


class OggError(Exception):
    """Файл не является корректным Ogg Vorbis."""


class OggCrcError(OggError):
    """CRC страницы Ogg не совпадает с данными."""


class CommentFormatError(ValueError):
    """Текст редактора нельзя превратить в комментарий."""


# ---------------------------------------------------------------- CRC


def _make_crc_table() -> Tuple[int, ...]:
    table = []
    for index in range(256):
        value = index << 24
        for _ in range(8):
            if value & 0x80000000:
                value = ((value << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                value = (value << 1) & 0xFFFFFFFF
        table.append(value)
    return tuple(table)


_CRC_TABLE = _make_crc_table()


def ogg_crc(data: bytes) -> int:
    """CRC страницы Ogg (полином 0x04C11DB7, без отражения)."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[
            ((crc >> 24) & 0xFF) ^ byte
        ]
    return crc


# ---------------------------------------------------------------- страницы


@dataclass(frozen=True)
class OggPage:
    header_type: int
    granule: int
    serial: int
    sequence: int
    lacing: Tuple[int, ...]
    payload: bytes
    crc_ok: bool = True
    offset: int = 0

    @property
    def continued(self) -> bool:
        return bool(self.header_type & 0x01)

    @property
    def first(self) -> bool:
        return bool(self.header_type & 0x02)

    @property
    def last(self) -> bool:
        return bool(self.header_type & 0x04)


def parse_pages(
    data: bytes,
    allow_truncated: bool = False,
) -> List[OggPage]:
    """Разобрать Ogg на страницы. Аудиоданные не интерпретируются.

    ``allow_truncated`` нужен для чтения только начала файла: последняя
    неполная страница просто игнорируется.
    """
    pages: List[OggPage] = []
    pos = 0
    total = len(data)

    while pos < total:
        if pos + 4 > total:
            if allow_truncated:
                break
        if data[pos:pos + 4] != CAPTURE:
            if allow_truncated:
                break
            raise OggError(
                "Потеряна сигнатура OggS со смещения %d" % pos
            )
        if pos + 27 > total:
            if allow_truncated:
                break
            raise OggError("Обрезанный заголовок Ogg-страницы")

        version = data[pos + 4]
        if version != 0:
            raise OggError("Неподдерживаемая версия Ogg: %d" % version)

        header_type = data[pos + 5]
        granule = struct.unpack_from("<q", data, pos + 6)[0]
        serial, sequence, stored_crc = struct.unpack_from(
            "<III",
            data,
            pos + 14,
        )
        segments = data[pos + 26]

        table_start = pos + 27
        table_end = table_start + segments
        if table_end > total:
            if allow_truncated:
                break
            raise OggError("Обрезана таблица сегментов Ogg-страницы")

        lacing = tuple(data[table_start:table_end])
        payload_length = sum(lacing)
        payload_start = table_end
        payload_end = payload_start + payload_length
        if payload_end > total:
            if allow_truncated:
                break
            raise OggError("Обрезаны данные Ogg-страницы")

        raw = bytearray(data[pos:payload_end])
        raw[22:26] = b"\0\0\0\0"
        crc_ok = ogg_crc(bytes(raw)) == stored_crc

        pages.append(
            OggPage(
                header_type=header_type,
                granule=granule,
                serial=serial,
                sequence=sequence,
                lacing=lacing,
                payload=data[payload_start:payload_end],
                crc_ok=crc_ok,
                offset=pos,
            )
        )
        pos = payload_end

    return pages


def _check_pages_crc(pages: Sequence[OggPage]) -> None:
    if not pages:
        raise OggError("Файл не содержит Ogg-страниц")

    for index, page in enumerate(pages):
        if page.crc_ok:
            continue
        raise OggCrcError(
            "CRC не совпадает: страница %d, serial 0x%08X, sequence %d, "
            "смещение 0x%X"
            % (index + 1, page.serial, page.sequence, page.offset)
        )


def validate_crc(data: bytes) -> None:
    """Проверить CRC всех страниц. Бросает :class:`OggCrcError`."""
    _check_pages_crc(parse_pages(data))


@dataclass(frozen=True)
class OggStreamInfo:
    """Метаданные первого Vorbis-потока."""

    serial: int
    channels: int
    sample_rate: int
    nominal_bitrate: int
    duration: float
    vendor: str
    comments_count: int
    crc_ok: bool = True

    @property
    def bitrate_kbps(self) -> float:
        return self.nominal_bitrate / 1000.0 if self.nominal_bitrate > 0 else 0.0


def read_stream_info(data: bytes) -> OggStreamInfo:
    pages = parse_pages(data)
    stream = _vorbis_stream(extract_streams(pages))
    if not stream.packets:
        raise OggError("В потоке Vorbis нет заголовка")

    ident = stream.packets[0]
    if len(ident) < 30:
        raise OggError("Обрезан identification-заголовок Vorbis")

    channels = ident[11]
    sample_rate = struct.unpack_from("<I", ident, 12)[0]
    nominal_bitrate = struct.unpack_from("<i", ident, 20)[0]

    comments = None
    if len(stream.packets) > 1 and stream.packets[1].startswith(
        VORBIS_COMMENT
    ):
        comments = parse_comment_packet(stream.packets[1])

    last_granule = 0
    for page in pages:
        if page.serial == stream.serial and page.granule >= 0:
            last_granule = max(last_granule, page.granule)

    duration = (
        float(last_granule) / float(sample_rate)
        if sample_rate > 0
        else 0.0
    )

    return OggStreamInfo(
        serial=stream.serial,
        channels=int(channels),
        sample_rate=int(sample_rate),
        nominal_bitrate=int(nominal_bitrate),
        duration=duration,
        vendor=comments.vendor if comments is not None else "",
        comments_count=len(comments.comments) if comments is not None else 0,
        crc_ok=all(page.crc_ok for page in pages),
    )


def serialize_page(page: OggPage) -> bytes:
    header = (
        CAPTURE
        + bytes((0, page.header_type & 0xFF))
        + struct.pack("<q", int(page.granule))
        + struct.pack("<III", page.serial, page.sequence, 0)
        + bytes((len(page.lacing),))
        + bytes(page.lacing)
        + page.payload
    )
    crc = ogg_crc(header)
    return header[:22] + struct.pack("<I", crc) + header[26:]


# ---------------------------------------------------------------- потоки


@dataclass(frozen=True)
class LogicalStream:
    serial: int
    packets: Tuple[bytes, ...]
    # Глобальные индексы страниц, на которых заканчивается каждый пакет.
    packet_end_pages: Tuple[int, ...]
    page_positions: Tuple[int, ...]


def extract_streams(pages: Sequence[OggPage]) -> Dict[int, LogicalStream]:
    """Восстановить пакеты каждого логического потока из страниц."""
    buffers: Dict[int, bytearray] = {}
    packets: Dict[int, List[bytes]] = {}
    ends: Dict[int, List[int]] = {}
    positions: Dict[int, List[int]] = {}

    for page_index, page in enumerate(pages):
        buffers.setdefault(page.serial, bytearray())
        packets.setdefault(page.serial, [])
        ends.setdefault(page.serial, [])
        positions.setdefault(page.serial, []).append(page_index)

        buffer = buffers[page.serial]
        offset = 0
        for segment in page.lacing:
            buffer.extend(page.payload[offset:offset + segment])
            offset += segment
            if segment < 255:
                packets[page.serial].append(bytes(buffer))
                ends[page.serial].append(page_index)
                buffer.clear()

    streams: Dict[int, LogicalStream] = {}
    for serial in packets:
        streams[serial] = LogicalStream(
            serial=serial,
            packets=tuple(packets[serial]),
            packet_end_pages=tuple(ends[serial]),
            page_positions=tuple(positions[serial]),
        )
    return streams


def _vorbis_stream(
    streams: Dict[int, LogicalStream],
) -> LogicalStream:
    for stream in streams.values():
        if stream.packets and stream.packets[0].startswith(VORBIS_ID):
            return stream
    raise OggError("В файле нет потока Vorbis")


# ---------------------------------------------------------------- комментарий


@dataclass(frozen=True)
class VorbisComments:
    vendor: str
    comments: Tuple[bytes, ...]
    framing: int = 1
    trailing: bytes = b""

    @property
    def engine_comment(self) -> bytes:
        return self.comments[0] if self.comments else b""


def parse_comment_packet(packet: bytes) -> VorbisComments:
    if not packet.startswith(VORBIS_COMMENT):
        raise OggError("Пакет не является Vorbis-комментарием")

    pos = len(VORBIS_COMMENT)
    if pos + 4 > len(packet):
        raise OggError("Обрезанный заголовок Vorbis-комментария")

    (vendor_length,) = struct.unpack_from("<I", packet, pos)
    pos += 4
    vendor_end = pos + vendor_length
    if vendor_end + 4 > len(packet):
        raise OggError("Обрезано имя кодировщика Vorbis")
    vendor = packet[pos:vendor_end].decode("utf-8", "replace")
    pos = vendor_end

    (count,) = struct.unpack_from("<I", packet, pos)
    pos += 4

    comments: List[bytes] = []
    for _ in range(count):
        if pos + 4 > len(packet):
            raise OggError("Обрезан список Vorbis-комментариев")
        (length,) = struct.unpack_from("<I", packet, pos)
        pos += 4
        end = pos + length
        if end > len(packet):
            raise OggError("Обрезан Vorbis-комментарий")
        comments.append(packet[pos:end])
        pos = end

    framing = packet[pos] if pos < len(packet) else 1
    trailing = packet[pos + 1:] if pos < len(packet) else b""
    return VorbisComments(
        vendor=vendor,
        comments=tuple(comments),
        framing=framing,
        trailing=trailing,
    )


def build_comment_packet(
    vendor: str,
    comments: Sequence[bytes],
    framing: int = 1,
    trailing: bytes = b"",
) -> bytes:
    vendor_bytes = str(vendor or "").encode("utf-8")
    parts = [
        VORBIS_COMMENT,
        struct.pack("<I", len(vendor_bytes)),
        vendor_bytes,
        struct.pack("<I", len(comments)),
    ]
    for comment in comments:
        parts.append(struct.pack("<I", len(comment)))
        parts.append(bytes(comment))
    parts.append(bytes((int(framing) & 0xFF,)))
    parts.append(bytes(trailing or b""))
    return b"".join(parts)


# ---------------------------------------------------------------- запись


def _packet_lacing(packet: bytes) -> List[int]:
    lacing = [255] * (len(packet) // 255)
    remainder = len(packet) % 255
    if remainder:
        lacing.append(remainder)
    else:
        # Нулевой сегмент завершает пакет, длина которого кратна 255.
        lacing.append(0)
    return lacing


def _packets_to_header_pages(
    packets: Sequence[bytes],
    serial: int,
    start_sequence: int,
) -> List[OggPage]:
    """Разложить заголовочные пакеты по страницам.

    Каждый пакет начинается на новой странице. Если пакет больше 255
    сегментов, он продолжается на следующих страницах с флагом
    continuation. Granule header-страниц равен 0.
    """
    pages: List[OggPage] = []
    sequence = start_sequence
    first_page = True

    for packet in packets:
        lacing = _packet_lacing(packet)
        segment_offset = 0
        payload_offset = 0

        while segment_offset < len(lacing):
            chunk = lacing[segment_offset:segment_offset + 255]
            chunk_length = sum(chunk)
            payload = packet[payload_offset:payload_offset + chunk_length]

            continuation = segment_offset > 0
            header_type = 0
            if first_page:
                header_type |= 0x02  # BOS
            if continuation:
                header_type |= 0x01

            last_chunk = segment_offset + 255 >= len(lacing)
            # Для страницы без завершённого пакета granule = -1.
            granule = 0 if last_chunk else -1

            pages.append(
                OggPage(
                    header_type=header_type,
                    granule=granule,
                    serial=serial,
                    sequence=sequence,
                    lacing=tuple(chunk),
                    payload=payload,
                )
            )
            sequence += 1
            first_page = False
            segment_offset += 255
            payload_offset += chunk_length

    return pages


def _shift_sequence(page: OggPage, delta: int) -> OggPage:
    if not delta:
        return page
    return OggPage(
        header_type=page.header_type,
        granule=page.granule,
        serial=page.serial,
        sequence=(page.sequence + delta) & 0xFFFFFFFF,
        lacing=page.lacing,
        payload=page.payload,
    )


def rewrite_engine_comment(
    data: bytes,
    engine_comment: bytes,
    vendor: Optional[str] = None,
) -> bytes:
    """Заменить первый Vorbis-комментарий, сохранив звук.

    Остальные комментарии, кодировщик и framing сохраняются.
    """
    pages = parse_pages(data)
    _check_pages_crc(pages)
    streams = extract_streams(pages)
    stream = _vorbis_stream(streams)

    if len(stream.packets) < 3:
        raise OggError("В потоке Vorbis меньше трёх заголовочных пакетов")
    if not stream.packets[1].startswith(VORBIS_COMMENT):
        raise OggError("Второй пакет не является Vorbis-комментарием")
    if not stream.packets[2].startswith(VORBIS_SETUP):
        raise OggError("Третий пакет не является setup-заголовком Vorbis")

    old = parse_comment_packet(stream.packets[1])
    comments = [bytes(engine_comment)]
    comments.extend(old.comments[1:])
    new_packet = build_comment_packet(
        vendor if vendor is not None else old.vendor,
        comments,
        framing=old.framing,
        trailing=old.trailing,
    )

    header_packets = [
        stream.packets[0],
        new_packet,
        stream.packets[2],
    ]
    new_header_pages = _packets_to_header_pages(
        header_packets,
        serial=stream.serial,
        start_sequence=0,
    )

    header_end_page = stream.packet_end_pages[2]
    if (
        len(stream.packet_end_pages) > 3
        and stream.packet_end_pages[3] <= header_end_page
    ):
        raise OggError(
            "Аудиопакет начинается на странице заголовков; "
            "безопасная замена комментария невозможна"
        )

    old_header_count = sum(
        1 for position in stream.page_positions
        if position <= header_end_page
    )
    delta = len(new_header_pages) - old_header_count
    replace_start = stream.page_positions[0]

    output: List[bytes] = []
    for page_index, page in enumerate(pages):
        if page_index == replace_start:
            output.extend(serialize_page(item) for item in new_header_pages)

        if (
            replace_start <= page_index <= header_end_page
            and page.serial == stream.serial
        ):
            continue

        if page.serial == stream.serial and page_index > header_end_page:
            page = _shift_sequence(page, delta)

        output.append(serialize_page(page))

    return b"".join(output)


def read_comments(data: bytes) -> VorbisComments:
    pages = parse_pages(data)
    stream = _vorbis_stream(extract_streams(pages))
    if len(stream.packets) < 2:
        raise OggError("В потоке Vorbis нет комментария")
    return parse_comment_packet(stream.packets[1])


def read_comments_prefix(data: bytes) -> VorbisComments:
    """Прочитать комментарий из начала файла, не разбирая весь поток."""
    pages = parse_pages(data, allow_truncated=True)
    stream = _vorbis_stream(extract_streams(pages))
    if len(stream.packets) < 2:
        raise OggError("В начале файла нет Vorbis-комментария")
    return parse_comment_packet(stream.packets[1])


def read_comments_from_file(path) -> VorbisComments:
    return read_comments(Path(path).read_bytes())


# ---------------------------------------------------------------- X-Ray


@dataclass(frozen=True)
class XraySoundComment:
    version: int = XRAY_VERSION
    min_dist: float = DEFAULT_MIN_DIST
    max_dist: float = DEFAULT_MAX_DIST
    base_volume: float = DEFAULT_BASE_VOLUME
    game_type: int = DEFAULT_GAME_TYPE
    max_ai_dist: float = DEFAULT_MAX_AI_DIST
    trailing: bytes = b""

    def effective_base_volume(self) -> float:
        return self.base_volume if self.version >= 2 else 1.0

    def effective_max_ai_dist(self) -> float:
        return self.max_ai_dist if self.version >= 3 else self.max_dist


def parse_xray_comment(raw: bytes) -> Optional[XraySoundComment]:
    """Разобрать бинарный комментарий движка или вернуть ``None``."""
    if len(raw) < 4:
        return None

    (version,) = struct.unpack_from("<I", raw, 0)
    size = _VERSION_SIZE.get(version)
    if size is None or len(raw) < size:
        return None

    min_dist, max_dist = struct.unpack_from("<ff", raw, 4)
    if version == 1:
        game_type = struct.unpack_from("<I", raw, 12)[0]
        return XraySoundComment(
            version=1,
            min_dist=min_dist,
            max_dist=max_dist,
            base_volume=1.0,
            game_type=game_type,
            max_ai_dist=max_dist,
            trailing=raw[size:],
        )

    base_volume = struct.unpack_from("<f", raw, 12)[0]
    game_type = struct.unpack_from("<I", raw, 16)[0]
    if version == 2:
        return XraySoundComment(
            version=2,
            min_dist=min_dist,
            max_dist=max_dist,
            base_volume=base_volume,
            game_type=game_type,
            max_ai_dist=max_dist,
            trailing=raw[size:],
        )

    max_ai_dist = struct.unpack_from("<f", raw, 20)[0]
    return XraySoundComment(
        version=3,
        min_dist=min_dist,
        max_dist=max_dist,
        base_volume=base_volume,
        game_type=game_type,
        max_ai_dist=max_ai_dist,
        trailing=raw[size:],
    )


def build_xray_comment(params: XraySoundComment) -> bytes:
    version = int(params.version)
    if version not in _VERSION_SIZE:
        raise CommentFormatError(
            "Версия комментария должна быть 1, 2 или 3"
        )

    parts = [
        struct.pack("<I", version),
        struct.pack("<ff", float(params.min_dist), float(params.max_dist)),
    ]
    if version >= 2:
        parts.append(struct.pack("<f", float(params.base_volume)))
        parts.append(struct.pack("<I", int(params.game_type)))
    else:
        parts.append(struct.pack("<I", int(params.game_type)))

    if version >= 3:
        parts.append(struct.pack("<f", float(params.max_ai_dist)))

    return b"".join(parts) + bytes(params.trailing or b"")


# ---------------------------------------------------------------- текст


_ALIASES = {
    "version": "version",
    "min_dist": "min_dist",
    "min_distance": "min_dist",
    "mindist": "min_dist",
    "max_dist": "max_dist",
    "max_distance": "max_dist",
    "maxdist": "max_dist",
    "base_volume": "base_volume",
    "volume": "base_volume",
    "basevolume": "base_volume",
    "game_type": "game_type",
    "gametype": "game_type",
    "max_ai_dist": "max_ai_dist",
    "max_ai_distance": "max_ai_dist",
    "maxaidist": "max_ai_dist",
}

_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*[:=]\s*(.*?)\s*$")
_GAME_TYPE_RE = re.compile(
    r"^\s*game_type\s*[:=]\s*(\S+)",
    re.IGNORECASE | re.MULTILINE,
)


def extract_game_type(text: str) -> Optional[int]:
    """Достать ``game_type`` из текста без полной валидации."""
    match = _GAME_TYPE_RE.search(str(text or ""))
    if match is None:
        return None
    try:
        return int(match.group(1), 0)
    except (TypeError, ValueError):
        return None


def replace_game_type(text: str, value: int) -> str:
    """Заменить строку ``game_type``, сохранив остальной текст."""
    source = str(text or "")
    line = "game_type = %d" % int(value)
    if _GAME_TYPE_RE.search(source):
        return _GAME_TYPE_RE.sub(line, source, count=1)
    if source and not source.endswith("\n"):
        source += "\n"
    return source + line + "\n"


def format_xray_comment(params: XraySoundComment) -> str:
    return "\n".join(
        (
            "# X-Ray OGG: параметры воспроизведения (бинарный Vorbis comment)",
            "# version 1: min_dist, max_dist, game_type",
            "# version 2: + base_volume",
            "# version 3: + max_ai_dist",
            "version = %d" % int(params.version),
            "min_dist = %s" % _format_float(params.min_dist),
            "max_dist = %s" % _format_float(params.max_dist),
            "base_volume = %s" % _format_float(params.base_volume),
            "game_type = %d" % int(params.game_type),
            "max_ai_dist = %s" % _format_float(params.max_ai_dist),
        )
    )


def _format_float(value: float) -> str:
    text = ("%.6f" % float(value)).rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def format_editor_text(comments: VorbisComments) -> str:
    """Текст для вкладки: бинарный комментарий или обычный текст."""
    raw = comments.engine_comment
    params = parse_xray_comment(raw)
    if params is not None:
        return format_xray_comment(params)
    if not raw:
        return format_xray_comment(XraySoundComment())
    return raw.decode("utf-8", "replace")


def parse_editor_text(text: str, xray_only: bool = False) -> bytes:
    """Превратить текст вкладки в байты первого Vorbis-комментария.

    Если в тексте есть известные поля X-Ray, он считается бинарным
    комментарием. Иначе сохраняется как обычный UTF-8 текст.
    ``xray_only=True`` запрещает переход бинарного комментария в текст:
    случайно удалённые поля не должны менять формат файла.
    """
    values = {}
    saw_field = False

    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue

        match = _KEY_RE.match(line)
        if match is None:
            continue

        key = _ALIASES.get(match.group(1).lower())
        if key is None:
            continue

        values[key] = match.group(2)
        saw_field = True

    if not saw_field:
        if xray_only:
            raise CommentFormatError(
                "Не найдено ни одного поля X-Ray "
                "(version, min_dist, max_dist, base_volume, game_type, "
                "max_ai_dist)"
            )
        return str(text or "").encode("utf-8")

    def number(name: str, default: float) -> float:
        value = values.get(name)
        if value is None or value == "":
            return default
        try:
            result = float(value.replace(",", "."))
        except ValueError:
            raise CommentFormatError(
                "Поле %s: ожидается число, получено %r" % (name, value)
            )
        if result != result or result in (float("inf"), float("-inf")):
            raise CommentFormatError("Поле %s: недопустимое число" % name)
        return result

    def integer(name: str, default: int, minimum: int, maximum: int) -> int:
        value = values.get(name)
        if value is None or value == "":
            return default
        try:
            result = int(value, 0)
        except ValueError:
            raise CommentFormatError(
                "Поле %s: ожидается целое число, получено %r" % (name, value)
            )
        if result < minimum or result > maximum:
            raise CommentFormatError(
                "Поле %s: допустимый диапазон %d..%d" % (
                    name,
                    minimum,
                    maximum,
                )
            )
        return result

    version = integer("version", XRAY_VERSION, 1, 3)
    min_dist = number("min_dist", DEFAULT_MIN_DIST)
    max_dist = number("max_dist", DEFAULT_MAX_DIST)
    base_volume = number("base_volume", DEFAULT_BASE_VOLUME)
    game_type = integer("game_type", DEFAULT_GAME_TYPE, 0, 0xFFFFFFFF)
    max_ai_dist = number("max_ai_dist", max_dist)

    if min_dist <= 0:
        raise CommentFormatError("min_dist должен быть больше нуля")
    if max_dist <= 0:
        raise CommentFormatError("max_dist должен быть больше нуля")
    if base_volume < 0:
        raise CommentFormatError("base_volume не может быть отрицательным")
    if max_ai_dist < 0:
        raise CommentFormatError("max_ai_dist не может быть отрицательным")

    return build_xray_comment(
        XraySoundComment(
            version=version,
            min_dist=min_dist,
            max_dist=max_dist,
            base_volume=base_volume,
            game_type=game_type,
            max_ai_dist=max_ai_dist,
        )
    )


def replace_engine_comment_text(data: bytes, text: str) -> bytes:
    """Собрать новый OGG по тексту вкладки."""
    return rewrite_engine_comment(data, parse_editor_text(text))


def describe_comment(comments: VorbisComments) -> str:
    """Короткая подпись для статуса вкладки."""
    params = parse_xray_comment(comments.engine_comment)
    if params is not None:
        return (
            "X-Ray v%d: min %.2f / max %.2f / volume %.2f / max AI %.2f"
            % (
                params.version,
                params.min_dist,
                params.max_dist,
                params.effective_base_volume(),
                params.effective_max_ai_dist(),
            )
        )
    if comments.engine_comment:
        return "Текстовый Vorbis comment"
    return "Комментарий отсутствует"

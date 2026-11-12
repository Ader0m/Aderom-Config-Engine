# -*- coding: utf-8 -*-
"""OGG/Vorbis: разбор и замена комментария без перекодирования звука."""

import struct
import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

try:
    import numpy as _numpy
    import soundfile as _soundfile
except Exception:  # pragma: no cover - необязательная зависимость
    _numpy = None
    _soundfile = None

from modules.ogg_editor.ogg import (
    OggCrcError,
    OggPage,
    XraySoundComment,
    _packets_to_header_pages,
    build_comment_packet,
    build_xray_comment,
    format_editor_text,
    parse_editor_text,
    parse_pages,
    parse_xray_comment,
    read_comments,
    read_stream_info,
    rewrite_engine_comment,
    serialize_page,
    validate_crc,
)


def _make_file(
    comment_packet,
    setup_packet=None,
    audio=((b"\x00" * 500, 1024), (b"\x01" * 500, 2048)),
):
    setup = setup_packet or (b"\x05vorbis" + b"\x11" * 200)
    serial = 77

    pages = _packets_to_header_pages(
        (
            b"\x01vorbis" + b"x" * 5,
            comment_packet,
            setup,
        ),
        serial=serial,
        start_sequence=0,
    )

    sequence = len(pages)
    for index, (packet, granule) in enumerate(audio):
        header_type = 0x04 if index == len(audio) - 1 else 0
        lacing = [255] * (len(packet) // 255) + [len(packet) % 255]
        pages.append(
            OggPage(
                header_type,
                granule,
                serial,
                sequence,
                tuple(lacing),
                packet,
            )
        )
        sequence += 1

    return b"".join(serialize_page(page) for page in pages)


class XrayCommentTest(unittest.TestCase):
    def test_versions_are_little_endian_structs(self):
        params = XraySoundComment(
            version=3,
            min_dist=2.5,
            max_dist=120.0,
            base_volume=0.75,
            game_type=7,
            max_ai_dist=80.0,
        )
        raw = build_xray_comment(params)

        self.assertEqual(len(raw), 24)
        self.assertEqual(struct.unpack_from("<I", raw, 0)[0], 3)
        self.assertAlmostEqual(struct.unpack_from("<f", raw, 4)[0], 2.5)
        self.assertAlmostEqual(struct.unpack_from("<f", raw, 8)[0], 120.0)
        self.assertAlmostEqual(struct.unpack_from("<f", raw, 12)[0], 0.75)
        self.assertEqual(struct.unpack_from("<I", raw, 16)[0], 7)
        self.assertAlmostEqual(struct.unpack_from("<f", raw, 20)[0], 80.0)

        parsed = parse_xray_comment(raw)
        self.assertEqual(parsed.version, 3)
        self.assertAlmostEqual(parsed.base_volume, 0.75)
        self.assertAlmostEqual(parsed.max_ai_dist, 80.0)

    def test_version_one_defaults(self):
        raw = build_xray_comment(
            XraySoundComment(
                version=1,
                min_dist=1.0,
                max_dist=25.0,
                game_type=2,
            )
        )
        self.assertEqual(len(raw), 16)
        parsed = parse_xray_comment(raw)
        self.assertEqual(parsed.version, 1)
        self.assertEqual(parsed.effective_base_volume(), 1.0)
        self.assertEqual(parsed.effective_max_ai_dist(), 25.0)

    def test_text_roundtrip(self):
        text = (
            "# comment\n"
            "version = 2\n"
            "min_dist = 1.5\n"
            "max_dist = 30\n"
            "base_volume = 0.5\n"
            "game_type = 4\n"
            "max_ai_dist = 20\n"
        )
        raw = parse_editor_text(text)
        parsed = parse_xray_comment(raw)

        self.assertEqual(parsed.version, 2)
        self.assertAlmostEqual(parsed.min_dist, 1.5)
        self.assertAlmostEqual(parsed.max_dist, 30.0)
        self.assertAlmostEqual(parsed.base_volume, 0.5)
        self.assertEqual(parsed.game_type, 4)
        self.assertEqual(len(raw), 20)

    def test_plain_text_stays_text(self):
        self.assertEqual(parse_editor_text("TITLE=Test\n"), b"TITLE=Test\n")


class OggRewriteTest(unittest.TestCase):
    def _original(self):
        comments = build_comment_packet(
            "test-encoder",
            (
                build_xray_comment(XraySoundComment(max_dist=50.0)),
                b"TITLE=Original",
            ),
        )
        return _make_file(comments)

    def test_reads_engine_comment_and_preserves_other_comments(self):
        data = self._original()
        comments = read_comments(data)

        self.assertEqual(comments.vendor, "test-encoder")
        self.assertEqual(len(comments.comments), 2)
        self.assertEqual(comments.comments[1], b"TITLE=Original")

        params = parse_xray_comment(comments.engine_comment)
        self.assertAlmostEqual(params.max_dist, 50.0)

    def test_replaces_comment_without_touching_audio(self):
        data = self._original()
        old_pages = parse_pages(data)

        new_raw = build_xray_comment(
            XraySoundComment(
                version=3,
                min_dist=3.0,
                max_dist=99.0,
                base_volume=0.4,
                game_type=1,
                max_ai_dist=70.0,
            )
        )
        rewritten = rewrite_engine_comment(data, new_raw)

        comments = read_comments(rewritten)
        params = parse_xray_comment(comments.engine_comment)
        self.assertAlmostEqual(params.min_dist, 3.0)
        self.assertAlmostEqual(params.max_dist, 99.0)
        self.assertAlmostEqual(params.base_volume, 0.4)
        self.assertEqual(params.game_type, 1)
        self.assertAlmostEqual(params.max_ai_dist, 70.0)
        self.assertEqual(comments.comments[1], b"TITLE=Original")

        new_pages = parse_pages(rewritten)
        old_audio = [
            page for page in old_pages if page.granule > 0
        ]
        new_audio = [
            page for page in new_pages if page.granule > 0
        ]
        self.assertEqual(len(old_audio), len(new_audio))
        for old, new in zip(old_audio, new_audio):
            self.assertEqual(old.payload, new.payload)
            self.assertEqual(old.granule, new.granule)
            self.assertEqual(old.serial, new.serial)
            self.assertTrue(new.last == old.last)

    def test_large_setup_header_is_repaged(self):
        setup = b"\x05vorbis" + b"\x22" * 70000
        comments = build_comment_packet(
            "enc",
            (build_xray_comment(XraySoundComment()),),
        )
        data = _make_file(comments, setup_packet=setup)
        rewritten = rewrite_engine_comment(
            data,
            build_xray_comment(XraySoundComment(max_dist=42.0)),
        )

        pages = parse_pages(rewritten)
        self.assertGreater(len(pages), 4)
        parsed = read_comments(rewritten)
        self.assertAlmostEqual(
            parse_xray_comment(parsed.engine_comment).max_dist,
            42.0,
        )
        audio = [page for page in pages if page.granule > 0]
        self.assertEqual(audio[0].payload, b"\x00" * 500)
        self.assertEqual(audio[-1].granule, 2048)

    def test_comment_text_view(self):
        data = self._original()
        text = format_editor_text(read_comments(data))
        self.assertIn("max_dist = 50", text)
        self.assertIn("version = 3", text)

    def test_corrupted_crc_blocks_rewrite(self):
        data = self._original()
        broken = bytearray(data)
        broken[-1] ^= 0xFF

        with self.assertRaises(OggCrcError):
            validate_crc(bytes(broken))

        with self.assertRaises(OggCrcError):
            rewrite_engine_comment(
                bytes(broken),
                build_xray_comment(XraySoundComment()),
            )

    @unittest.skipIf(
        _soundfile is None or _numpy is None,
        "soundfile/numpy не установлены",
    )
    def test_real_vorbis_file_stays_decodable(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "real.ogg"
            samples = _numpy.zeros((44100 // 10, 2), dtype="int16")
            _soundfile.write(
                str(source),
                samples,
                44100,
                format="OGG",
                subtype="VORBIS",
            )

            data = source.read_bytes()
            self.assertTrue(read_comments(data).comments)

            info = read_stream_info(data)
            self.assertEqual(info.channels, 2)
            self.assertEqual(info.sample_rate, 44100)
            self.assertGreater(info.duration, 0.0)
            self.assertTrue(info.serial)
            self.assertTrue(info.crc_ok)
            self.assertTrue(info.vendor)

            rewritten = rewrite_engine_comment(
                data,
                build_xray_comment(
                    XraySoundComment(
                        min_dist=5.0,
                        max_dist=60.0,
                        base_volume=0.6,
                    )
                ),
            )

            target = Path(d) / "real.out.ogg"
            target.write_bytes(rewritten)

            decoded, samplerate = _soundfile.read(
                str(target),
                dtype="int16",
            )
            self.assertEqual(samplerate, 44100)
            self.assertEqual(decoded.shape, samples.shape)

            parsed = parse_xray_comment(
                read_comments(rewritten).engine_comment
            )
            self.assertAlmostEqual(parsed.min_dist, 5.0)
            self.assertAlmostEqual(parsed.base_volume, 0.6)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Секции звуков: поля и поиск путей к .ogg."""

import tempfile
import unittest
from pathlib import Path

from tests.support import SRC  # noqa: F401

from sdk import decode_item_section, encode_item_section
from sdk import GraphNode, SectionGraph, SectionGraphRequest
from modules.ogg_editor.section_links import OggSectionGraphContributor
from modules.ogg_editor.sections import (
    extract_sound_paths,
    extract_sound_references,
    normalize_section_name,
    parse_section_field_refs,
    parse_section_fields,
    section_label,
)


class OggSectionTest(unittest.TestCase):
    def test_parse_section_fields_stops_at_next_section(self):
        text = """
[other]
x = 1
[snd_test]:parent
snd_shoot = weapon\\shot
snd_bad = other\\missing
; comment
[another]
y = 2
"""
        fields = parse_section_fields(text, "SND_TEST")
        self.assertEqual(
            fields,
            [
                ("snd_shoot", "weapon\\shot"),
                ("snd_bad", "other\\missing"),
            ],
        )

    def test_section_name_accepts_brackets(self):
        self.assertEqual(normalize_section_name("wpn"), "wpn")
        self.assertEqual(normalize_section_name("[wpn]"), "wpn")
        self.assertEqual(normalize_section_name(" [wpn] "), "wpn")
        self.assertEqual(normalize_section_name("[wpn]:parent"), "wpn")
        self.assertEqual(section_label("wpn"), "[wpn]")
        self.assertEqual(section_label("[wpn]"), "[wpn]")

        text = """
[base]
snd_shoot = weapon\\shot
[wpn]:base
cost = 1
"""
        for name in ("wpn", "[wpn]", "[wpn]:base"):
            self.assertEqual(
                dict(parse_section_fields(text, name)),
                {"snd_shoot": "weapon\\shot", "cost": "1"},
            )

    def test_section_fields_include_inherited_snd_fields(self):
        text = """
[base_sounds]
snd_shoot = weapons\\ak\\ak_shoot
snd_reload = weapons\\ak\\ak_reload
[child_section]:base_sounds
cost = 100
"""
        fields = parse_section_fields(text, "child_section")
        values = dict(fields)
        self.assertEqual(values["cost"], "100")
        self.assertEqual(values["snd_shoot"], "weapons\\ak\\ak_shoot")
        self.assertEqual(values["snd_reload"], "weapons\\ak\\ak_reload")

    def test_snd_field_can_point_to_file_without_subfolder(self):
        with tempfile.TemporaryDirectory() as d:
            sounds = Path(d) / "gamedata" / "sounds"
            sounds.mkdir(parents=True)
            (sounds / "wpn_ak74_snd_shoot.ogg").write_bytes(b"x")

            paths = extract_sound_paths(
                (("snd_shoot", "wpn_ak74_snd_shoot"),),
                sounds,
            )
            self.assertEqual(
                [path.name for path in paths],
                ["wpn_ak74_snd_shoot.ogg"],
            )

    def test_item_section_drag_payload_roundtrip(self):
        payload = encode_item_section(
            "wpn_test",
            (
                ("snd_shoot", "weapon\\shot"),
                ("cost", "100"),
            ),
        )
        decoded = decode_item_section(payload)

        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.section, "wpn_test")
        self.assertEqual(
            decoded.fields,
            (
                ("snd_shoot", "weapon\\shot"),
                ("cost", "100"),
            ),
        )

    def test_extract_sound_references_keeps_fields_and_lines(self):
        with tempfile.TemporaryDirectory() as d:
            sounds = Path(d) / "gamedata" / "sounds"
            weapon = sounds / "weapon"
            weapon.mkdir(parents=True)
            shot = weapon / "shot.ogg"
            shot.write_bytes(b"x")

            text = """
[base]
snd_shoot = weapon\\shot
[wpn]:base
snd_reload = weapon\\shot
"""
            references = extract_sound_references(text, "wpn", sounds)
            self.assertEqual(len(references), 2)
            self.assertEqual(
                [ref.field for ref in references],
                ["snd_shoot", "snd_reload"],
            )
            self.assertEqual(
                [ref.line_number for ref in references],
                [3, 5],
            )
            self.assertTrue(
                all(ref.path == shot for ref in references)
            )

    def test_section_graph_contributor_returns_sound_nodes(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / "gamedata"
            sounds = game / "sounds"
            weapon = sounds / "weapon"
            weapon.mkdir(parents=True)
            shot = weapon / "shot.ogg"
            shot.write_bytes(b"x")

            text = """
[wpn]
snd_shoot = weapon\\shot
snd_reload = weapon\\shot
"""
            ltx_path = game / "configs" / "weapons.ltx"
            ltx_path.parent.mkdir(parents=True)
            ltx_path.write_text(text, encoding="utf-8")

            other_ltx = game / "configs" / "weapons_override.ltx"
            other_ltx.write_text(
                "[wpn]\nsnd_lost = weapon\\lost\n",
                encoding="utf-8",
            )
            lost_ogg = weapon / "lost.ogg"
            lost_ogg.write_bytes(b"x")

            class Services:
                def forest_scan_dir(self, _forest):
                    return ltx_path.parent

                def game_root(self, _forest):
                    return game

                def assemble_resolved(self, *_args, **_kwargs):
                    return text, []

            class Ctx:
                services = Services()

            contributor = OggSectionGraphContributor(Ctx())
            graph = SectionGraph(
                section_name="wpn",
                root=GraphNode(
                    display_name="wpn",
                    file_path=ltx_path,
                    first_match_line=2,
                ),
                children=(
                    GraphNode(
                        display_name="weapons_override.ltx",
                        file_path=other_ltx,
                        first_match_line=1,
                    ),
                ),
            )
            request = SectionGraphRequest(
                forest=None,
                section_name="wpn",
                graph=graph,
            )

            contribution = contributor(request)

            self.assertIn(
                "sound_reference",
                contribution.edge_styles,
            )
            self.assertIn(
                "sound_reference",
                contribution.openers,
            )
            self.assertEqual(len(contribution.children), 1)
            node = contribution.children[0]
            self.assertEqual(node.edge_types, ("sound_reference",))
            self.assertEqual(node.target_path, shot)
            self.assertEqual(node.raw_ref, "weapon\\shot")
            self.assertEqual(node.matches_count, 2)
            self.assertEqual(
                [match.line_number for match in node.matches],
                [3, 4],
            )

            vfs_contribution = contributor(
                SectionGraphRequest(
                    forest=None,
                    section_name="wpn",
                    graph=graph,
                    vfs_mode=True,
                )
            )
            # В VFS-режиме левый граф уже заморожен: модуль отдаёт
            # только потерянные звуки.
            self.assertEqual(vfs_contribution.children, ())
            self.assertEqual(len(vfs_contribution.lost_children), 1)
            self.assertEqual(
                vfs_contribution.lost_children[0].target_path,
                lost_ogg,
            )
            self.assertFalse(
                vfs_contribution.lost_children[0].resolved
            )

    def test_extract_sound_paths_uses_sounds_root_heuristic(self):
        with tempfile.TemporaryDirectory() as d:
            sounds = Path(d) / "gamedata" / "sounds"
            weapon = sounds / "weapon"
            sub = weapon / "sub"
            sub.mkdir(parents=True)
            (weapon / "shot.ogg").write_bytes(b"x")
            (weapon / "other.ogg").write_bytes(b"x")
            (sub / "deep.ogg").write_bytes(b"x")

            fields = [
                ("snd_shoot", "weapon\\shot"),
                ("snd_glob", "weapon\\*.ogg"),
                ("snd_dir", "sounds\\weapon\\sub"),
                ("snd_bad", "unknown\\shot"),
                ("snd_notes", "ordinary text"),
            ]
            paths = extract_sound_paths(fields, sounds)

            names = sorted(path.name for path in paths)
            self.assertEqual(
                names,
                ["deep.ogg", "other.ogg", "shot.ogg"],
            )


if __name__ == "__main__":
    unittest.main()

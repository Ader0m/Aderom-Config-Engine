# -*- coding: utf-8 -*-
"""Тесты публичного контракта модулей (SDK)."""

import unittest

from tests.support import SRC  # noqa: F401

from sdk.adapters import (
    CommandApiAdapter,
    CoreApiAdapter,
    EventApiAdapter,
    TaskApiAdapter,
    UiApiAdapter,
    _to_graph_node_view,
)
from sdk import DEFAULT_SEARCH_DELAY_MS
from sdk import GraphNode, SectionGraph, SectionGraphRequest
from sdk import GraphEdgeStyle, SectionGraphContribution
from sdk.contracts import ModuleBase, ModuleContext, ModuleManifest
from ui.bus import CommandBus, EventBus
from ui.debounce import DEFAULT_SEARCH_DELAY_MS as UI_SEARCH_DELAY_MS


class _Ui:
    def __init__(self):
        self.logs = []

    def log(self, message):
        self.logs.append(message)


class _Tasks:
    def run(self, *args, **kwargs):
        return None


class _Services:
    pass


class ModuleApiTest(unittest.TestCase):
    def test_search_debounce_standard_is_shared(self):
        self.assertEqual(DEFAULT_SEARCH_DELAY_MS, 180)
        self.assertEqual(UI_SEARCH_DELAY_MS, DEFAULT_SEARCH_DELAY_MS)

    def test_manifest_defaults(self):
        manifest = ModuleManifest(name="test_module")

        self.assertEqual(manifest.name, "test_module")
        self.assertEqual(manifest.version, "0.0.0")
        self.assertEqual(manifest.sdk_min, "0.1.0")
        self.assertEqual(manifest.description, "")
        self.assertEqual(manifest.author, "")
        self.assertEqual(manifest.requires, ())

    def test_module_base_lifecycle_methods_are_callable(self):
        module = ModuleBase()
        ui = _Ui()
        context = ModuleContext(
            "0.1.0",
            EventBus(),
            CommandBus(),
            ui,
            _Tasks(),
            _Services(),
            None,
        )

        module.init(context)
        module.register(context)
        module.activate(context)
        module.deactivate(context)

    def test_context_wraps_buses_and_is_not_global(self):
        first = ModuleContext(
            "0.1.0",
            EventBus(),
            CommandBus(),
            _Ui(),
            _Tasks(),
            _Services(),
            None,
        )
        second = ModuleContext(
            "0.1.0",
            EventBus(),
            CommandBus(),
            _Ui(),
            _Tasks(),
            _Services(),
            None,
        )

        self.assertIsInstance(first.events, EventApiAdapter)
        self.assertIsInstance(first.commands, CommandApiAdapter)
        self.assertIsInstance(first.ui, UiApiAdapter)
        self.assertIsInstance(first.tasks, TaskApiAdapter)
        self.assertIsInstance(first.services, CoreApiAdapter)
        self.assertIsNot(first.events, second.events)
        self.assertIsNot(first.commands, second.commands)
        self.assertIsNot(first.ui, second.ui)

    def test_section_graph_contributors_are_collected(self):
        class Provider:
            pass

        provider = Provider()
        adapter = CoreApiAdapter(provider)
        second_adapter = CoreApiAdapter(provider)
        node = GraphNode(
            display_name="shot.ogg",
            file_path=None,
            first_match_line=1,
            matches=(),
        )
        root = GraphNode(
            display_name="wpn",
            file_path=None,
            first_match_line=1,
        )
        request = SectionGraphRequest(
            forest=None,
            section_name="wpn",
            graph=SectionGraph(section_name="wpn", root=root),
        )

        adapter.register_section_graph_contributor(
            lambda _request: (node,)
        )
        self.assertEqual(
            adapter.collect_section_graph_nodes(request),
            (node,),
        )
        # Второй ModuleContext получает свой адаптер, но реестр общий.
        self.assertEqual(
            second_adapter.collect_section_graph_nodes(request),
            (node,),
        )

        logs = []
        adapter.register_section_graph_contributor(
            lambda _request: 1 / 0
        )
        self.assertEqual(
            adapter.collect_section_graph_nodes(request, log=logs.append),
            (node,),
        )
        self.assertEqual(len(logs), 1)

        self.assertTrue(
            adapter.unregister_section_graph_contributor(
                adapter._section_graph_contributors[0]
            )
        )

    def test_section_graph_contribution_carries_lost_nodes_and_styles(self):
        class Provider:
            pass

        adapter = CoreApiAdapter(Provider())
        root = GraphNode(
            display_name="wpn",
            file_path=None,
            first_match_line=1,
        )
        found = GraphNode(
            display_name="shot.ogg",
            file_path=None,
            first_match_line=2,
            matches=(),
        )
        lost = GraphNode(
            display_name="old.ogg",
            file_path=None,
            first_match_line=3,
            matches=(),
        )
        style = GraphEdgeStyle("Звук", "graph_sound")
        opener = lambda _node: None
        adapter.register_section_graph_contributor(
            lambda _request: SectionGraphContribution(
                children=(found,),
                lost_children=(lost,),
                edge_styles={"sound_reference": style},
                openers={"sound_reference": opener},
            )
        )

        contribution = adapter.collect_section_graph_contribution(
            SectionGraphRequest(
                forest=None,
                section_name="wpn",
                graph=SectionGraph(section_name="wpn", root=root),
            )
        )

        self.assertEqual(contribution.children, (found,))
        self.assertEqual(contribution.lost_children, (lost,))
        self.assertEqual(
            contribution.edge_styles,
            {"sound_reference": style},
        )
        self.assertEqual(
            contribution.openers,
            {"sound_reference": opener},
        )

    def test_inheritance_direction_survives_sdk_conversion(self):
        class Match:
            edge_type = "inheritance"
            line_number = 10
            line_text = "[wpn]:base"
            related = "base"
            related_is_parent = True

        class Node:
            display_name = "base"
            file_path = None
            first_match_line = 10
            matches = [Match()]
            raw_ref = None
            resolved = False
            target_path = None
            physical_path = None
            related_file_path = None
            related_line = None
            related_resolved = False

        view = _to_graph_node_view(Node())
        self.assertTrue(view.matches[0].related_is_parent)
        self.assertTrue(view.primary_edge_type == "inheritance")

    def test_context_log_delegates_to_ui(self):
        ui = _Ui()
        context = ModuleContext(
            "0.1.0",
            EventBus(),
            CommandBus(),
            ui,
            _Tasks(),
            _Services(),
            None,
        )

        context.log("hello")

        self.assertEqual(ui.logs, ["hello"])


if __name__ == "__main__":
    unittest.main()

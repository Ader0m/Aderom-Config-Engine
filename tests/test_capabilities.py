# -*- coding: utf-8 -*-
"""Тесты capability registry и sdk_max."""

import unittest

from tests.support import SRC  # noqa: F401

from sdk.kernel import ModuleManifest
from sdk.kernel.capabilities import (
    CapabilityConflictError,
    CapabilityRegistry,
    CapabilityScope,
    CapabilityUnavailable,
    CapabilityVersionError,
    MissingCapability,
)
from sdk.kernel.levels import check_manifest


class CapabilityRegistryTest(unittest.TestCase):
    def test_provide_require_and_optional(self):
        registry = CapabilityRegistry()
        provider = CapabilityScope(registry, "items")
        provider.provide("items.catalog", "1.2.0", {"items": []})

        consumer = CapabilityScope(registry, "browser")
        self.assertEqual(
            consumer.require("items.catalog", min_version="1.0"),
            {"items": []},
        )
        self.assertEqual(
            consumer.optional("missing.capability"),
            None,
        )
        self.assertTrue(consumer.has("items.catalog", min_version="1.2.0"))
        self.assertFalse(consumer.has("items.catalog", min_version="2.0.0"))
        self.assertEqual(consumer.version("items.catalog"), "1.2.0")
        self.assertEqual(consumer.provider("items.catalog"), "items")

    def test_missing_and_version_errors(self):
        registry = CapabilityRegistry()
        consumer = CapabilityScope(registry, "demo")

        with self.assertRaises(MissingCapability):
            consumer.require("absent")
        with self.assertRaises(CapabilityUnavailable):
            registry.declare("declared.only", "1.0.0", owner="demo")
            consumer.require("declared.only")
        with self.assertRaises(CapabilityVersionError):
            consumer.provide("demo.cap", "1.0.0", object())
            consumer.require("demo.cap", min_version="2.0.0")

    def test_remove_owner_removes_only_its_capabilities(self):
        registry = CapabilityRegistry()
        first = CapabilityScope(registry, "first")
        second = CapabilityScope(registry, "second")
        first.provide("first.cap", "1.0.0", object())
        second.provide("second.cap", "1.0.0", object())

        self.assertEqual(registry.remove_owner("first"), 1)
        self.assertFalse(registry.has("first.cap"))
        self.assertTrue(registry.has("second.cap"))

    def test_provide_cannot_replace_another_owner(self):
        registry = CapabilityRegistry()
        first = CapabilityScope(registry, "first")
        second = CapabilityScope(registry, "second")
        first_obj = object()
        second_obj = object()
        first.provide("shared.cap", "1.0.0", first_obj)

        with self.assertRaises(CapabilityConflictError):
            second.provide("shared.cap", "2.0.0", second_obj)

        self.assertEqual(registry.provider("shared.cap"), "first")
        self.assertIs(registry.optional("shared.cap"), first_obj)

    def test_declare_detects_provider_conflict(self):
        registry = CapabilityRegistry()
        registry.declare("shared.cap", "1.0.0", owner="first")

        with self.assertRaises(CapabilityConflictError):
            registry.declare("shared.cap", "1.0.0", owner="second")

        self.assertEqual(registry.provider("shared.cap"), "first")

    def test_discard_removes_capability(self):
        registry = CapabilityRegistry()
        registry.declare("demo.cap", "1.0.0", owner="demo")

        self.assertTrue(registry.discard("demo.cap"))
        self.assertFalse(registry.has("demo.cap"))
        self.assertFalse(registry.discard("demo.cap"))


class ManifestSdkMaxTest(unittest.TestCase):
    def test_sdk_max_is_checked(self):
        self.assertTrue(
            check_manifest(
                ModuleManifest(name="demo", sdk_min="0.1.0", sdk_max="0.3.0"),
                "0.3.0",
            )
        )
        self.assertFalse(
            check_manifest(
                ModuleManifest(name="demo", sdk_min="0.1.0", sdk_max="0.2.9"),
                "0.3.0",
            )
        )
        self.assertFalse(
            check_manifest(
                ModuleManifest(name="demo", sdk_min="0.4.0"),
                "0.3.0",
            )
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from importlib.metadata import entry_points

from inspect_ai.util._sandbox.registry import registry_find_sandboxenv

import inspect_briq
from inspect_briq import BriqSandboxEnvironment


def test_briq_is_registered_with_inspect() -> None:
    assert registry_find_sandboxenv("briq") is BriqSandboxEnvironment


def test_the_entry_point_loads_the_registering_module() -> None:
    (entry,) = [e for e in entry_points(group="inspect_ai") if e.name == "inspect_briq"]
    assert entry.value == "inspect_briq._registry"
    assert entry.load().BriqSandboxEnvironment is BriqSandboxEnvironment


def test_the_package_exports_its_config() -> None:
    assert inspect_briq.BriqSandboxEnvironmentConfig.__name__ == "BriqSandboxEnvironmentConfig"

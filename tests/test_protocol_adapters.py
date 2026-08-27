from types import SimpleNamespace

import pytest

from amp.protocol_adapters import ProtocolAdapterRegistry


def test_registry_selects_one_adapter_for_version_family():
    registry = ProtocolAdapterRegistry({
        "versions": {
            "26.1": {"family": "java-26.1", "status": "supported"},
            "26.1.1": {"family": "java-26.1", "status": "supported"},
        }
    })
    adapter = SimpleNamespace(family="java-26.1")

    registry.register(adapter)

    assert registry.for_version("26.1") is adapter
    assert registry.for_version("26.1.1") is adapter


def test_registry_rejects_unknown_version_and_missing_family():
    registry = ProtocolAdapterRegistry({
        "versions": {"26.2": {"family": "java-26.2", "status": "pending"}}
    })

    with pytest.raises(ValueError, match="Unknown Minecraft version"):
        registry.for_version("26.3")
    with pytest.raises(ValueError, match="No protocol adapter"):
        registry.for_version("26.2")


def test_registry_rejects_duplicate_family_registration():
    registry = ProtocolAdapterRegistry({"versions": {}})
    registry.register(SimpleNamespace(family="java-26.1"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(SimpleNamespace(family="java-26.1"))

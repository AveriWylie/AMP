"""Select version-specific protocol behavior by declared codec family."""

from typing import Protocol

from version_support import load_support_manifest


class ProtocolAdapter(Protocol):
    family: str

    def login_start(self, identity) -> bytes: ...

    def handle_login(self, packet_id, payload, session): ...

    def handle_configuration(self, packet_id, payload): ...

    def decode_play(self, packet_id, payload): ...

    def encode_action(self, action, world_state, game_mode) -> object: ...


class ProtocolAdapterRegistry:
    def __init__(self, manifest=None):
        self._manifest = manifest or load_support_manifest()
        self._adapters = {}

    def register(self, adapter):
        family = adapter.family
        if family in self._adapters:
            raise ValueError(f"Protocol adapter already registered: {family}")
        self._adapters[family] = adapter

    def for_version(self, version):
        if version == self._manifest.get("legacy_reference"):
            family = self._manifest["legacy_family"]
        else:
            try:
                family = self._manifest["versions"][version]["family"]
            except KeyError as error:
                raise ValueError(f"Unknown Minecraft version: {version}") from error
        try:
            return self._adapters[family]
        except KeyError as error:
            raise ValueError(
                f"No protocol adapter registered for {version} ({family})"
            ) from error

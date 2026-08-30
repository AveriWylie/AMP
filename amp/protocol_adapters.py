
# imports
from typing import Protocol
from amp.version_support import load_support_manifest


"""
--------------------------------------------------------------------------------------------
Class Header - Protocol adapter contract
--------------------------------------------------------------------------------------------
The shape every version adapter has to satisfy. It is a typing Protocol rather than a base
class on purpose, adapters are matched structurally, so nothing has to inherit from this and
nothing breaks if an adapter is written independently. It documents the contract and lets a
type checker enforce it without forcing an inheritance tree.

Five methods because that is where version differences actually land. Login and Configuration
change shape between releases, clientbound decoding changes packet IDs and field layouts, and
serverbound encoding changes how actions are framed. Everything else in AMP is version
neutral and sits above this line.
--------------------------------------------------------------------------------------------
"""
class ProtocolAdapter(Protocol):

    family: str

    def login_start(self, identity) -> bytes: ...

    def handle_login(self, packet_id, payload, session): ...

    def handle_configuration(self): ...

    def decode_play(self, packet_id, payload): ...

    def encode_action(self, action, world_state, game_mode) -> object: ...


"""
--------------------------------------------------------------------------------------------
Class Header - Adapter registry
--------------------------------------------------------------------------------------------
Maps a Minecraft version to the adapter that speaks its protocol. The indirection exists
because versions and adapters are not one to one, several releases can share one codec family
so they share one adapter, which is why the manifest stores a family per version rather than
an adapter per version.

Lookup is therefore two hops, version to family through the manifest, then family to adapter
through whatever registered itself. Each hop fails with its own message because they are
different problems. An unknown version means the manifest was never told about the release,
an unregistered family means the release is known but nothing implements it yet.
--------------------------------------------------------------------------------------------
"""
class ProtocolAdapterRegistry:

    def __init__(self, manifest=None):
        self._manifest = manifest or load_support_manifest()
        self._adapters = {}


    # Refuses to overwrite. A silent replacement would mean whichever module imported last
    # decides the protocol, which is an import-order bug you would only find in gameplay.
    def register(self, adapter):
        family = adapter.family

        if family in self._adapters:
            raise ValueError(f"Protocol adapter already registered: {family}")

        self._adapters[family] = adapter


    def for_version(self, version):
        try:
            family = self._manifest["versions"][version]["family"]
        except KeyError as error:
            raise ValueError(f"Unknown Minecraft version: {version}") from error

        try:
            return self._adapters[family]
        except KeyError as error:
            raise ValueError(f"No protocol adapter registered for {version} ({family})") from error

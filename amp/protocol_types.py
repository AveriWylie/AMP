"""
--------------------------------------------------------------------------------------------
Protocol Types Module - The version-neutral vocabulary
--------------------------------------------------------------------------------------------
Every type that crosses the line between protocol code and everything else. Adapters decode
packets into the event types below and encode the action types back into packets, so world
state, gameplay, pathfinding and planning never see a packet ID or a byte layout.

This is what makes a new Minecraft version a change to one adapter rather than a change
everywhere. When a packet's fields move or its ID shifts, the adapter absorbs it and the
types here stay the same.

Everything is a frozen dataclass. Frozen because these are messages, not state, an event
describes something that already happened and an action describes something to do, so nothing
downstream should be editing one in place. It also means they hash, so they can go in sets and
be compared cheaply. The mutable world lives in world_state, and it is built by reading these,
never by holding them.

Three groups follow, clientbound events, serverbound actions, and the encoded packet wrapper.
The two functions at the bottom convert between actions and the plain dicts the command queue
and the planner use.
--------------------------------------------------------------------------------------------
"""

# imports
from dataclasses import dataclass
from typing import Any


"""
--------------------------------------------------------------------------------------------
Class Field Header - Clientbound events
--------------------------------------------------------------------------------------------
What the server told us. Produced by an adapter's decode_play and consumed by world_state.

The two position types are separate on purpose. PositionChanged is us, and carries
relative_flags because the server sets one bit per axis, so a single message can be relative
in yaw and absolute in x. EntityMoved and EntityTeleported are other things, and are split
because a delta needs an existing position to apply to while a teleport does not, so world
state has to treat them differently.

InventoryReplaced and SlotChanged both carry state_id, the server's sequence number for the
window, which is how a late reply is recognised as stale and dropped. SlotChanged's is
optional because not every slot update is sequenced.

ChunkLoaded types its chunk as Any rather than importing Chunk, which keeps this module free
of any dependency on the parser and avoids a cycle between the two.
--------------------------------------------------------------------------------------------
"""
@dataclass(frozen=True)
class PositionChanged:
    x: float
    y: float
    z: float
    yaw: float = 0.0
    pitch: float = 0.0
    # one bit per field, so a single packet can be relative in some axes and absolute in others
    relative_flags: int = 0


@dataclass(frozen=True)
class HealthChanged:
    health: float
    food: int
    saturation: float


# dimension_id rides along because a respawn arrives as this packet too, and a dimension that
# differs from the last one means the world is being replaced rather than the player moved
@dataclass(frozen=True)
class SelfEntityIdentified:
    entity_id: int
    dimension_id: int | None = None


# Sent when the tracked world is no longer valid, on death or a dimension change. Carries no
# state of its own, it is the signal to discard chunks and entities rather than to rebuild
# from, since everything the server sends afterwards is the new world.
@dataclass(frozen=True)
class WorldReset:
    dimension_id: int | None = None


@dataclass(frozen=True)
class EntitySpawned:
    entity_id: int
    uuid: str
    type_id: int
    name: str
    x: float
    y: float
    z: float


# a delta, so it needs a known position to apply to, unlike the teleport below
@dataclass(frozen=True)
class EntityMoved:
    entity_id: int
    dx: float
    dy: float
    dz: float


@dataclass(frozen=True)
class EntityTeleported:
    entity_id: int
    x: float
    y: float
    z: float


# batched, the server removes several entities in one packet when a chunk unloads
@dataclass(frozen=True)
class EntitiesRemoved:
    entity_ids: tuple[int, ...]


# chunk is Any rather than Chunk, so this module never imports the parser and cannot cycle
@dataclass(frozen=True)
class ChunkLoaded:
    chunk_x: int
    chunk_z: int
    chunk: Any


@dataclass(frozen=True)
class BlockChanged:
    x: int
    y: int
    z: int
    state_id: int

    # convenience for callers that key by position, saves rebuilding the tuple at each site
    @property
    def position(self):
        return self.x, self.y, self.z


@dataclass(frozen=True)
class InventoryReplaced:
    window_id: int
    # server sequence number for the window, older than what is held means stale
    state_id: int
    slots: tuple[tuple[int, Any], ...]
    carried: Any = None


@dataclass(frozen=True)
class SlotChanged:
    window_id: int
    # optional here, not every single-slot update carries a sequence number
    state_id: int | None
    slot: int
    item: Any


@dataclass(frozen=True)
class HotbarSelected:
    slot: int


"""
--------------------------------------------------------------------------------------------
Class Field Header - Serverbound actions
--------------------------------------------------------------------------------------------
What we want to do. Produced from planner or gameplay commands and consumed by an adapter's
encode_action.

Defaults carry vanilla client behaviour so callers only state what they actually care about.
face=1 is the top of a block, which is the usual mining and placing surface, and hand=0 is the
main hand.

MineAction's duration is how long to hold the break, worked out from mining_data rather than
guessed, and PlaceAction carries target and block purely so execution can confirm afterwards
that the right block appeared in the right place. Neither is part of the packet itself, they
travel with the action because the layer that verifies the result is not the layer that knows
what was intended.
--------------------------------------------------------------------------------------------
"""
@dataclass(frozen=True)
class MoveAction:
    x: float
    y: float
    z: float
    # the server uses this to decide whether to apply its own gravity, so a jump or a fall has
    # to say False or the position gets corrected straight back down
    on_ground: bool = True


@dataclass(frozen=True)
class ChatAction:
    message: str


@dataclass(frozen=True)
class LookAction:
    yaw: float
    pitch: float


@dataclass(frozen=True)
class SwingAction:
    hand: int = 0


@dataclass(frozen=True)
class SneakAction:
    sneaking: bool = True


@dataclass(frozen=True)
class AttackAction:
    entity_id: int


@dataclass(frozen=True)
class MineAction:
    x: int
    y: int
    z: int
    # face 1 is the top of the block, the usual surface to mine from
    face: int = 1
    # how long to hold the break, computed from mining_data rather than guessed
    duration: float = 0


@dataclass(frozen=True)
class PlaceAction:
    x: int
    y: int
    z: int
    face: int = 1
    # target and block are not sent, they ride along so execution can confirm the result
    target: tuple[int, int, int] = ()
    block: str | None = None


@dataclass(frozen=True)
class UseItemAction:
    hand: int = 0


@dataclass(frozen=True)
class SelectHotbarAction:
    slot: int


@dataclass(frozen=True)
class SwapHotbarAction:
    source_slot: int
    hotbar_slot: int


"""
--------------------------------------------------------------------------------------------
Class Field Header - Encoded output
--------------------------------------------------------------------------------------------
What an adapter hands back. One action can become several packets, placing a block may need a
hotbar select first, so EncodedAction is a sequence rather than a single packet.

delay_before exists because some sequences are rejected when they arrive together. The server
expects a gap between selecting a slot and using it, so the pacing has to travel with the
packets, the executor cannot know which pairs need spacing without knowing the protocol.
--------------------------------------------------------------------------------------------
"""
@dataclass(frozen=True)
class PacketStep:
    packet: bytes
    # some packets are dropped when they arrive too close together, so pacing rides along
    delay_before: float = 0


@dataclass(frozen=True)
class EncodedAction:
    steps: tuple[PacketStep, ...]


"""
--------------------------------------------------------------------------------------------
Function Field Header - Command conversion
--------------------------------------------------------------------------------------------
Bridges the typed actions above and the plain dicts used by the command queue and the planner.
Dicts exist on that side because model replies arrive as JSON and the queue is inspected and
logged, both of which are easier with ordinary data. Types exist on this side because encoding
wants named fields it can rely on.

Both directions end in a raise rather than a default. An unrecognised action is a bug in the
caller, and silently dropping it would mean a command that vanishes with no error and no
effect, which is far harder to find than an exception at the boundary.

The required fields are indexed and the optional ones use get with a default, so a command
missing something essential fails here, at conversion, rather than encoding a malformed packet
and failing somewhere less obvious.
--------------------------------------------------------------------------------------------
"""
def action_from_command(command):
    action = command.get("action")

    if action == "move":
        return MoveAction(command["x"], command["y"], command["z"],command.get("on_ground", True))

    if action == "chat":
        return ChatAction(command["message"])

    if action == "look":
        return LookAction(command["yaw"], command["pitch"])

    if action == "swing":
        return SwingAction(command.get("hand", 0))

    if action == "sneak":
        return SneakAction(command.get("sneaking", True))

    if action == "attack":
        return AttackAction(command["entity_id"])

    if action == "mine":
        return MineAction(command["x"], command["y"], command["z"], command.get("face", 1), command.get("duration", 0))

    if action == "place":
        return PlaceAction(
            command["x"], command["y"], command["z"],
            command.get("face", 1),
            tuple(command.get("target", ())),
            command.get("block")
        )

    if action == "use_item":
        return UseItemAction(command.get("hand", 0))

    if action == "select_hotbar":
        return SelectHotbarAction(command["slot"])

    if action == "swap_hotbar":
        return SwapHotbarAction(command["source_slot"], command["hotbar_slot"])

    raise ValueError(f"Unsupported action: {action!r}")


# The reverse trip, used where a typed action has to go back through the dict-shaped queue.
# isinstance rather than matching on a field, since the type is the tag here.
def command_from_action(action):

    if isinstance(action, MoveAction):
        command = {"action": "move", "x": action.x, "y": action.y, "z": action.z}

        # only written when it differs from the default, so ordinary walking commands stay
        # the same shape they have always been
        if not action.on_ground:
            command["on_ground"] = False

        return command

    if isinstance(action, ChatAction):
        return {"action": "chat", "message": action.message}

    if isinstance(action, LookAction):
        return {"action": "look", "yaw": action.yaw, "pitch": action.pitch}

    if isinstance(action, SwingAction):
        return {"action": "swing", "hand": action.hand}

    if isinstance(action, SneakAction):
        return {"action": "sneak", "sneaking": action.sneaking}

    if isinstance(action, AttackAction):
        return {"action": "attack", "entity_id": action.entity_id}

    if isinstance(action, MineAction):
        return {"action": "mine", "x": action.x, "y": action.y, "z": action.z,
                "face": action.face, "duration": action.duration}

    if isinstance(action, PlaceAction):
        return {"action": "place", "x": action.x, "y": action.y, "z": action.z,
                "face": action.face, "target": action.target, "block": action.block}

    if isinstance(action, UseItemAction):
        return {"action": "use_item", "hand": action.hand}

    if isinstance(action, SelectHotbarAction):
        return {"action": "select_hotbar", "slot": action.slot}

    if isinstance(action, SwapHotbarAction):
        return {"action": "swap_hotbar", "source_slot": action.source_slot, "hotbar_slot": action.hotbar_slot}

    raise TypeError(f"Unsupported action value: {type(action).__name__}")

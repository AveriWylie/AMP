"""Version-neutral values exchanged across protocol and gameplay boundaries."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PositionChanged:
    x: float
    y: float
    z: float
    yaw: float = 0.0
    pitch: float = 0.0
    relative_flags: int = 0


@dataclass(frozen=True)
class HealthChanged:
    health: float
    food: int
    saturation: float


@dataclass(frozen=True)
class SelfEntityIdentified:
    entity_id: int
    dimension_id: int | None = None


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


@dataclass(frozen=True)
class EntitiesRemoved:
    entity_ids: tuple[int, ...]


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

    @property
    def position(self):
        return self.x, self.y, self.z


@dataclass(frozen=True)
class InventoryReplaced:
    window_id: int
    state_id: int
    slots: tuple[tuple[int, Any], ...]
    carried: Any = None


@dataclass(frozen=True)
class SlotChanged:
    window_id: int
    state_id: int | None
    slot: int
    item: Any


@dataclass(frozen=True)
class HotbarSelected:
    slot: int


@dataclass(frozen=True)
class MoveAction:
    x: float
    y: float
    z: float
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
    face: int = 1
    duration: float = 0


@dataclass(frozen=True)
class PlaceAction:
    x: int
    y: int
    z: int
    face: int = 1
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


@dataclass(frozen=True)
class PacketStep:
    packet: bytes
    delay_before: float = 0


@dataclass(frozen=True)
class EncodedAction:
    steps: tuple[PacketStep, ...]


def action_from_command(command):
    action = command.get("action")
    if action == "move":
        return MoveAction(
            command["x"], command["y"], command["z"],
            command.get("on_ground", True),
        )
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
        return MineAction(
            command["x"], command["y"], command["z"],
            command.get("face", 1), command.get("duration", 0),
        )
    if action == "place":
        return PlaceAction(
            command["x"], command["y"], command["z"],
            command.get("face", 1), tuple(command.get("target", ())),
            command.get("block"),
        )
    if action == "use_item":
        return UseItemAction(command.get("hand", 0))
    if action == "select_hotbar":
        return SelectHotbarAction(command["slot"])
    if action == "swap_hotbar":
        return SwapHotbarAction(command["source_slot"], command["hotbar_slot"])
    raise ValueError(f"Unsupported action: {action!r}")


def command_from_action(action):
    if isinstance(action, MoveAction):
        command = {
            "action": "move", "x": action.x, "y": action.y, "z": action.z
        }
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
        return {
            "action": "mine", "x": action.x, "y": action.y, "z": action.z,
            "face": action.face, "duration": action.duration,
        }
    if isinstance(action, PlaceAction):
        return {
            "action": "place", "x": action.x, "y": action.y, "z": action.z,
            "face": action.face, "target": action.target, "block": action.block,
        }
    if isinstance(action, UseItemAction):
        return {"action": "use_item", "hand": action.hand}
    if isinstance(action, SelectHotbarAction):
        return {"action": "select_hotbar", "slot": action.slot}
    if isinstance(action, SwapHotbarAction):
        return {
            "action": "swap_hotbar", "source_slot": action.source_slot,
            "hotbar_slot": action.hotbar_slot,
        }
    raise TypeError(f"Unsupported action value: {type(action).__name__}")

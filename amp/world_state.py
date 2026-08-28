"""Apply version-neutral protocol events to AMP's live world state."""

from amp.protocol_types import (
    BlockChanged,
    ChunkLoaded,
    EntitiesRemoved,
    EntityMoved,
    EntitySpawned,
    EntityTeleported,
    HealthChanged,
    HotbarSelected,
    InventoryReplaced,
    PositionChanged,
    SelfEntityIdentified,
    SlotChanged,
)


class WorldStateTracker:
    def __init__(self, protocol_adapter, connection):
        self.protocol_adapter = protocol_adapter
        self.connection = connection
        self.state = {
            "position": {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0},
            "position_revision": 0,
            "health": 20.0,
            "food": 20,
            "entities": {},
            "self_entity_id": None,
            "map": {},
            "blocks": {},
            "inventory": {
                "slots": {}, "selected_hotbar_slot": 0, "carried": None, "state_id": 0
            },
        }

    def _on_packet(self, packet_id, payload):
        for event in self.protocol_adapter.decode_play(packet_id, payload):
            self.apply(event)

    def apply(self, event):
        if isinstance(event, PositionChanged):
            previous = self.state["position"]
            self.state["position"] = {
                "x": previous["x"] + event.x if event.relative_flags & 1 else event.x,
                "y": previous["y"] + event.y if event.relative_flags & 2 else event.y,
                "z": previous["z"] + event.z if event.relative_flags & 4 else event.z,
                "yaw": previous["yaw"] + event.yaw if event.relative_flags & 8 else event.yaw,
                "pitch": previous["pitch"] + event.pitch if event.relative_flags & 16 else event.pitch,
            }
            self.state["position_revision"] += 1
        elif isinstance(event, HealthChanged):
            self.state["health"] = event.health
            self.state["food"] = event.food
            if event.health <= 0:
                self._respawn()
        elif isinstance(event, SelfEntityIdentified):
            self.state["self_entity_id"] = event.entity_id
        elif isinstance(event, EntitySpawned):
            self.state["entities"][event.entity_id] = {
                "uuid": event.uuid, "type": event.type_id, "name": event.name,
                "x": event.x, "y": event.y, "z": event.z,
            }
        elif isinstance(event, EntityMoved):
            entity = self.state["entities"].get(event.entity_id)
            if entity is not None:
                entity["x"] += event.dx
                entity["y"] += event.dy
                entity["z"] += event.dz
        elif isinstance(event, EntityTeleported):
            entity = self.state["entities"].get(event.entity_id)
            if entity is not None:
                entity.update({"x": event.x, "y": event.y, "z": event.z})
        elif isinstance(event, EntitiesRemoved):
            for entity_id in event.entity_ids:
                self.state["entities"].pop(entity_id, None)
        elif isinstance(event, ChunkLoaded):
            self.state["map"][(event.chunk_x, event.chunk_z)] = event.chunk
        elif isinstance(event, BlockChanged):
            self._apply_block_change(event)
        elif isinstance(event, InventoryReplaced):
            if event.window_id == 0:
                inventory = self.state["inventory"]
                if event.state_id >= inventory["state_id"]:
                    inventory.update({
                        "slots": dict(event.slots), "carried": event.carried,
                        "state_id": event.state_id,
                    })
        elif isinstance(event, SlotChanged):
            self._apply_slot_change(event)
        elif isinstance(event, HotbarSelected):
            self.state["inventory"]["selected_hotbar_slot"] = event.slot
        else:
            raise TypeError(f"Unsupported world event: {type(event).__name__}")

    def _respawn(self):
        packet_id = self.connection._encode_varint(self.connection.play_ids["client_command"])
        data = self.connection._encode_varint(0)
        self.connection._send(self.connection._encode_varint(len(packet_id + data)) + packet_id + data)
        self.state["health"] = 20.0
        self.state["food"] = 20

    def _apply_block_change(self, event):
        chunk = self.state["map"].get((event.x >> 4, event.z >> 4))
        if chunk is None:
            return
        section_y = (event.y + 64) >> 4
        if section_y in chunk._sections:
            section = chunk._sections[section_y]
            section.setdefault("patched", {})[
                (event.x & 0xF, event.y & 0xF, event.z & 0xF)
            ] = event.state_id

    def _apply_slot_change(self, event):
        inventory = self.state["inventory"]
        if event.window_id in (0, -2) and event.slot >= 0:
            if (event.window_id == 0 and event.state_id is not None
                    and event.state_id < inventory["state_id"]):
                return
            if event.item is None:
                inventory["slots"].pop(event.slot, None)
            else:
                inventory["slots"][event.slot] = event.item
            if event.state_id is not None:
                inventory["state_id"] = event.state_id
        elif event.window_id == -1 and event.slot == -1:
            inventory["carried"] = event.item

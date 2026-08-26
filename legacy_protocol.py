"""Temporary 1.20.2 protocol adapter used to characterize the migration baseline."""

import struct
import time
import uuid

from chunk import Chunk
from connection import Connection
from entity_data import entity_name
from inventory_data import item_name
from protocol_data import packet_ids_for_protocol
from protocol_types import (
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
    AttackAction,
    ChatAction,
    EncodedAction,
    LookAction,
    MineAction,
    MoveAction,
    PacketStep,
    PlaceAction,
    SelectHotbarAction,
    SneakAction,
    SwapHotbarAction,
    SwingAction,
    UseItemAction,
)


class LegacyProtocolAdapter:
    family = "legacy-1.20.2"

    def __init__(self, version, connection, play_ids):
        self.version = version
        self.connection = connection
        self.play_ids = play_ids
        self.serverbound_ids = packet_ids_for_protocol(
            connection._protocol_version, "serverbound"
        )
        self._sequence = 0

    def _packet(self, name, data):
        packet_id = self.connection._encode_varint(self.serverbound_ids[name])
        return self.connection._encode_varint(len(packet_id + data)) + packet_id + data

    def _next_sequence(self):
        value = self._sequence
        self._sequence += 1
        return value

    @staticmethod
    def _packed_position(x, y, z):
        return ((x & 0x3FFFFFF) << 38) | ((z & 0x3FFFFFF) << 12) | (y & 0xFFF)

    def encode_action(self, action, world_state, game_mode):
        encode = self.connection._encode_varint
        if isinstance(action, MoveAction):
            packet = self._packet("position", struct.pack(">ddd?", action.x, action.y, action.z, True))
        elif isinstance(action, ChatAction):
            message = action.message.encode("utf-8")
            data = (encode(len(message)) + message + struct.pack(">q", int(time.time() * 1000))
                    + struct.pack(">q", 0) + encode(0) + encode(0) + b"\x00" * 3)
            packet = self._packet("chat_message", data)
        elif isinstance(action, LookAction):
            packet = self._packet("look", struct.pack(">ff?", action.yaw, action.pitch, True))
        elif isinstance(action, SwingAction):
            packet = self._packet("arm_animation", encode(action.hand))
        elif isinstance(action, SneakAction):
            entity_id = (world_state or {}).get("self_entity_id") or 0
            data = encode(entity_id) + encode(0 if action.sneaking else 1) + encode(0)
            packet = self._packet("entity_action", data)
        elif isinstance(action, AttackAction):
            packet = self._packet("use_entity", encode(action.entity_id) + encode(1) + b"\x00")
        elif isinstance(action, MineAction):
            def digging(status):
                data = (encode(status) + struct.pack(">Q", self._packed_position(action.x, action.y, action.z))
                        + struct.pack(">b", action.face) + encode(self._next_sequence()))
                return self._packet("block_dig", data)
            steps = [PacketStep(digging(0))]
            if game_mode != "creative":
                steps.append(PacketStep(digging(2), action.duration))
            return EncodedAction(tuple(steps))
        elif isinstance(action, PlaceAction):
            data = (encode(0) + struct.pack(">Q", self._packed_position(action.x, action.y, action.z))
                    + encode(action.face) + struct.pack(">fff", .5, .5, .5) + b"\x00"
                    + encode(self._next_sequence()))
            packet = self._packet("block_place", data)
        elif isinstance(action, UseItemAction):
            packet = self._packet("use_item", encode(action.hand) + encode(self._next_sequence()))
        elif isinstance(action, SelectHotbarAction):
            if action.slot not in range(9):
                raise ValueError("Hotbar slot must be between 0 and 8")
            packet = self._packet("held_item_slot", struct.pack(">h", action.slot))
        elif isinstance(action, SwapHotbarAction):
            packet = self._encode_hotbar_swap(action, world_state)
        else:
            raise TypeError(f"Unsupported action value: {type(action).__name__}")
        return EncodedAction((PacketStep(packet),))

    def _encode_slot(self, item):
        if item is None:
            return b"\x00"
        if "wire" in item:
            return bytes.fromhex(item["wire"])
        return (b"\x01" + self.connection._encode_varint(item["id"])
                + struct.pack(">b", item["count"]) + b"\x00")

    def _encode_hotbar_swap(self, action, world_state):
        if action.source_slot not in range(9, 36):
            raise ValueError("Source slot must be in the player main inventory (9-35)")
        if action.hotbar_slot not in range(9):
            raise ValueError("Hotbar slot must be between 0 and 8")
        inventory = world_state["inventory"]
        destination_slot = 36 + action.hotbar_slot
        source_item = inventory["slots"].get(action.source_slot)
        destination_item = inventory["slots"].get(destination_slot)
        encode = self.connection._encode_varint
        data = (b"\x00" + encode(inventory["state_id"])
                + struct.pack(">h", action.source_slot) + struct.pack(">b", action.hotbar_slot)
                + encode(2) + encode(2)
                + struct.pack(">h", action.source_slot) + self._encode_slot(destination_item)
                + struct.pack(">h", destination_slot) + self._encode_slot(source_item) + b"\x00")
        return self._packet("window_click", data)

    def decode_play(self, packet_id, payload):
        if packet_id == self.play_ids["position"]:
            return [self._position(payload)]
        if packet_id == self.play_ids["update_health"]:
            return [self._health(payload)]
        if packet_id == self.play_ids["spawn_entity"]:
            return [self._entity(payload)]
        if packet_id == self.play_ids["login"]:
            return [SelfEntityIdentified(struct.unpack_from(">i", payload, 0)[0])]
        if packet_id in (self.play_ids["rel_entity_move"], self.play_ids["entity_move_look"]):
            return [self._entity_move(payload)]
        if packet_id == self.play_ids["entity_teleport"]:
            return [self._entity_teleport(payload)]
        if packet_id == self.play_ids["entity_destroy"]:
            return [self._entities_removed(payload)]
        if packet_id == self.play_ids["map_chunk"]:
            return [self._chunk(payload)]
        if packet_id == self.play_ids["block_change"]:
            return [self._block(payload)]
        if packet_id == self.play_ids["window_items"]:
            return [self._window_items(payload)]
        if packet_id == self.play_ids["set_slot"]:
            return [self._set_slot(payload)]
        if packet_id == self.play_ids["held_item_slot"]:
            return [HotbarSelected(struct.unpack(">b", payload)[0])]
        return []

    def _position(self, payload):
        x, y, z = struct.unpack_from(">ddd", payload, 0)
        yaw, pitch = struct.unpack_from(">ff", payload, 24)
        teleport_id, _ = Connection._decode_varint_bytes(payload, 33)
        packet_id = self.connection._encode_varint(self.connection.play_ids["teleport_confirm"])
        data = self.connection._encode_varint(teleport_id)
        self.connection._send(self.connection._encode_varint(len(packet_id + data)) + packet_id + data)
        return PositionChanged(x, y, z, yaw, pitch)

    @staticmethod
    def _health(payload):
        health = struct.unpack_from(">f", payload, 0)[0]
        food, consumed = Connection._decode_varint_bytes(payload, 4)
        saturation = struct.unpack_from(">f", payload, 4 + consumed)[0]
        return HealthChanged(health, food, saturation)

    def _entity(self, payload):
        entity_id, consumed = Connection._decode_varint_bytes(payload, 0)
        uuid_offset = consumed
        entity_uuid = str(uuid.UUID(bytes=payload[uuid_offset:uuid_offset + 16]))
        entity_type, consumed = Connection._decode_varint_bytes(payload, uuid_offset + 16)
        offset = uuid_offset + 16 + consumed
        x, y, z = struct.unpack_from(">ddd", payload, offset)
        return EntitySpawned(
            entity_id, entity_uuid, entity_type, entity_name(self.version, entity_type),
            x, y, z,
        )

    @staticmethod
    def _entity_move(payload):
        entity_id, consumed = Connection._decode_varint_bytes(payload, 0)
        dx, dy, dz = struct.unpack_from(">hhh", payload, consumed)
        return EntityMoved(entity_id, dx / 4096, dy / 4096, dz / 4096)

    @staticmethod
    def _entity_teleport(payload):
        entity_id, consumed = Connection._decode_varint_bytes(payload, 0)
        x, y, z = struct.unpack_from(">ddd", payload, consumed)
        return EntityTeleported(entity_id, x, y, z)

    @staticmethod
    def _entities_removed(payload):
        count, consumed = Connection._decode_varint_bytes(payload, 0)
        offset = consumed
        entity_ids = []
        for _ in range(count):
            entity_id, consumed = Connection._decode_varint_bytes(payload, offset)
            offset += consumed
            entity_ids.append(entity_id)
        return EntitiesRemoved(tuple(entity_ids))

    def _chunk(self, payload):
        chunk_x, chunk_z = struct.unpack_from(">ii", payload, 0)
        return ChunkLoaded(chunk_x, chunk_z, Chunk(payload[8:], self.version))

    @staticmethod
    def _block(payload):
        packed = struct.unpack_from(">q", payload, 0)[0]
        x = packed >> 38
        z = (packed >> 12) & 0x3FFFFFF
        y = packed & 0xFFF
        if x >= 1 << 25:
            x -= 1 << 26
        if z >= 1 << 25:
            z -= 1 << 26
        if y >= 1 << 11:
            y -= 1 << 12
        state_id, _ = Connection._decode_varint_bytes(payload, 8)
        return BlockChanged(x, y, z, state_id)

    @staticmethod
    def _skip_nbt_payload(data, offset, tag_type):
        fixed_sizes = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}
        if tag_type in fixed_sizes:
            return offset + fixed_sizes[tag_type]
        if tag_type == 7:
            return offset + 4 + struct.unpack_from(">i", data, offset)[0]
        if tag_type == 8:
            return offset + 2 + struct.unpack_from(">H", data, offset)[0]
        if tag_type == 9:
            child_type = data[offset]
            length = struct.unpack_from(">i", data, offset + 1)[0]
            offset += 5
            for _ in range(length):
                offset = LegacyProtocolAdapter._skip_nbt_payload(data, offset, child_type)
            return offset
        if tag_type == 10:
            while True:
                child_type = data[offset]
                offset += 1
                if child_type == 0:
                    return offset
                name_length = struct.unpack_from(">H", data, offset)[0]
                offset = LegacyProtocolAdapter._skip_nbt_payload(
                    data, offset + 2 + name_length, child_type
                )
        if tag_type in (11, 12):
            length = struct.unpack_from(">i", data, offset)[0]
            return offset + 4 + length * (4 if tag_type == 11 else 8)
        raise ValueError(f"Unknown inventory NBT tag type: {tag_type}")

    def _decode_slot(self, payload, offset):
        slot_start = offset
        present = payload[offset] != 0
        offset += 1
        if not present:
            return None, offset
        item_id, consumed = Connection._decode_varint_bytes(payload, offset)
        offset += consumed
        count = struct.unpack_from(">b", payload, offset)[0]
        offset += 1
        nbt_type = payload[offset]
        offset += 1
        if nbt_type:
            offset = self._skip_nbt_payload(payload, offset, nbt_type)
        return {
            "id": item_id, "name": item_name(self.version, item_id), "count": count,
            "wire": payload[slot_start:offset].hex(),
        }, offset

    def _window_items(self, payload):
        window_id = payload[0]
        state_id, consumed = Connection._decode_varint_bytes(payload, 1)
        offset = 1 + consumed
        count, consumed = Connection._decode_varint_bytes(payload, offset)
        offset += consumed
        slots = []
        for slot_index in range(count):
            item, offset = self._decode_slot(payload, offset)
            if item is not None:
                slots.append((slot_index, item))
        carried, _ = self._decode_slot(payload, offset)
        return InventoryReplaced(window_id, state_id, tuple(slots), carried)

    def _set_slot(self, payload):
        window_id = struct.unpack_from(">b", payload, 0)[0]
        state_id, consumed = Connection._decode_varint_bytes(payload, 1)
        offset = 1 + consumed
        slot = struct.unpack_from(">h", payload, offset)[0]
        item, _ = self._decode_slot(payload, offset + 2)
        return SlotChanged(window_id, state_id, slot, item)

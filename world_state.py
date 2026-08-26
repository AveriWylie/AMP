"""Decode clientbound packets into AMP's live world state."""

import struct
import uuid

from chunk import Chunk
from connection import Connection
from entity_data import entity_name
from inventory_data import item_name


class WorldStateTracker:
    def __init__(self, version, connection, play_ids):
        self.version = version
        self.connection = connection
        self.play_ids = play_ids
        self.state = {
            "position": {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0},
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
        if packet_id == self.play_ids["position"]:
            self._handle_position(payload)
        elif packet_id == self.play_ids["update_health"]:
            self._handle_health(payload)
        elif packet_id == self.play_ids["spawn_entity"]:
            self._handle_entity(payload)
        elif packet_id == self.play_ids["login"]:
            self.state["self_entity_id"] = struct.unpack_from(">i", payload, 0)[0]
        elif packet_id in (
            self.play_ids["rel_entity_move"], self.play_ids["entity_move_look"]
        ):
            self._handle_entity_move(payload)
        elif packet_id == self.play_ids["entity_teleport"]:
            self._handle_entity_teleport(payload)
        elif packet_id == self.play_ids["entity_destroy"]:
            self._handle_entity_destroy(payload)
        elif packet_id == self.play_ids["map_chunk"]:
            self._handle_chunk(payload)
        elif packet_id == self.play_ids["block_change"]:
            self._handle_block_update(payload)
        elif packet_id == self.play_ids["window_items"]:
            self._handle_window_items(payload)
        elif packet_id == self.play_ids["set_slot"]:
            self._handle_set_slot(payload)
        elif packet_id == self.play_ids["held_item_slot"]:
            self.state["inventory"]["selected_hotbar_slot"] = struct.unpack(">b", payload)[0]

    # x, y, z are 8-byte doubles, yaw and pitch are 4-byte floats
    # all big-endian
    def _handle_position(self, payload):
        x, y, z = struct.unpack_from(">ddd", payload, 0)
        yaw, pitch = struct.unpack_from(">ff", payload, 24)
        self.state["position"] = {
            "x": x, "y": y, "z": z,
            "yaw": yaw, "pitch": pitch
        }
        # must confirm position back to server or it will kick you
        self._confirm_position(payload)

    # server sends a flags byte at offset 32 and a VarInt teleport ID at offset 33
    # we must echo it back with packet 0x00 (confirm teleport)
    def _confirm_position(self, payload):
        teleport_id, _ = Connection._decode_varint_bytes(payload, 33)
        packet_id = self.connection._encode_varint(
            self.connection.play_ids["teleport_confirm"]
        )
        data = self.connection._encode_varint(teleport_id)
        length = self.connection._encode_varint(len(packet_id + data))
        self.connection._send(length + packet_id + data)

    # respawn handling goes here later
    def _handle_health(self, payload):
        health = struct.unpack_from(">f", payload, 0)[0]
        food, _ = Connection._decode_varint_bytes(payload, 4)
        self.state["health"] = health
        self.state["food"] = food

        if health <= 0:
            print("Bot has died")
            self._respawn()

    def _respawn(self):
        # Client Status packet 0x07, action 0 = perform respawn
        packet_id = self.connection._encode_varint(
            self.connection.play_ids["client_command"]
        )
        data = self.connection._encode_varint(0)
        length = self.connection._encode_varint(len(packet_id + data))
        self.connection._send(length + packet_id + data)
        # reset world state health and food to full after respawn request sent
        self.state["health"] = 20.0
        self.state["food"] = 20
        print("Respawn sent")

    # Spawn Entity: VarInt id, 16-byte UUID, VarInt type, then three doubles
    def _handle_entity(self, payload):
        entity_id, consumed = Connection._decode_varint_bytes(payload, 0)
        entity_uuid_offset = consumed
        entity_uuid_end = entity_uuid_offset + 16
        entity_uuid = str(uuid.UUID(bytes=payload[entity_uuid_offset:entity_uuid_end]))
        entity_type, consumed = Connection._decode_varint_bytes(payload, entity_uuid_end)
        position_offset = entity_uuid_end + consumed
        x, y, z = struct.unpack_from(">ddd", payload, position_offset)
        self.state["entities"][entity_id] = {
            "uuid": entity_uuid,
            "type": entity_type,
            "name": entity_name(self.version, entity_type),
            "x": x, "y": y, "z": z
        }

    def _handle_entity_move(self, payload):
        entity_id, consumed = Connection._decode_varint_bytes(payload, 0)
        entity = self.state["entities"].get(entity_id)
        if entity is None:
            return
        dx, dy, dz = struct.unpack_from(">hhh", payload, consumed)
        entity["x"] += dx / 4096
        entity["y"] += dy / 4096
        entity["z"] += dz / 4096

    def _handle_entity_teleport(self, payload):
        entity_id, consumed = Connection._decode_varint_bytes(payload, 0)
        entity = self.state["entities"].get(entity_id)
        if entity is None:
            return
        entity["x"], entity["y"], entity["z"] = struct.unpack_from(">ddd", payload, consumed)

    def _handle_entity_destroy(self, payload):
        count, consumed = Connection._decode_varint_bytes(payload, 0)
        offset = consumed
        for _ in range(count):
            entity_id, consumed = Connection._decode_varint_bytes(payload, offset)
            offset += consumed
            self.state["entities"].pop(entity_id, None)

    def _handle_chunk(self, payload):
        cx = struct.unpack_from(">i", payload, 0)[0]
        cz = struct.unpack_from(">i", payload, 4)[0]
        # chunk data blob starts at byte 8, rest of payload is heightmap NBT + sections
        chunk_data = payload[8:]
        self.state["map"][(cx, cz)] = Chunk(chunk_data, self.version)

    def _handle_block_update(self, payload):
        # position packed as a single big-endian long: x<<38 | z<<12 | y
        packed = struct.unpack_from(">q", payload, 0)[0]
        x = packed >> 38
        z = (packed >> 12) & 0x3FFFFFF
        y = packed & 0xFFF
        # sign-extend x/z from 26-bit signed and y from 12-bit signed
        if x >= (1 << 25): x -= (1 << 26)
        if z >= (1 << 25): z -= (1 << 26)
        if y >= (1 << 11): y -= (1 << 12)

        cx = x >> 4
        cz = z >> 4
        chunk = self.state["map"].get((cx, cz))

        if chunk is None:
            return
        # new state id follows the position long as a varint
        state_id, _ = Connection._decode_varint_bytes(payload, 8)
        # patch the block into the chunk's section directly
        section_y = (y + 64) >> 4
        if section_y in chunk._sections:
            chunk._sections[section_y]["patched"] = chunk._sections[section_y].get("patched", {})
            chunk._sections[section_y]["patched"][(x & 0xF, y & 0xF, z & 0xF)] = state_id

    @staticmethod
    def _skip_nbt_payload(data, offset, tag_type):
        fixed_sizes = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}
        if tag_type in fixed_sizes:
            return offset + fixed_sizes[tag_type]
        if tag_type == 7:
            length = struct.unpack_from(">i", data, offset)[0]
            return offset + 4 + length
        if tag_type == 8:
            length = struct.unpack_from(">H", data, offset)[0]
            return offset + 2 + length
        if tag_type == 9:
            child_type = data[offset]
            length = struct.unpack_from(">i", data, offset + 1)[0]
            offset += 5
            for _ in range(length):
                offset = WorldStateTracker._skip_nbt_payload(data, offset, child_type)
            return offset
        if tag_type == 10:
            while True:
                child_type = data[offset]
                offset += 1
                if child_type == 0:
                    return offset
                name_length = struct.unpack_from(">H", data, offset)[0]
                offset += 2 + name_length
                offset = WorldStateTracker._skip_nbt_payload(data, offset, child_type)
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

    def _handle_window_items(self, payload):
        window_id = payload[0]
        state_id, consumed = Connection._decode_varint_bytes(payload, 1)
        offset = 1 + consumed
        count, consumed = Connection._decode_varint_bytes(payload, offset)
        offset += consumed
        slots = {}
        for slot_index in range(count):
            item, offset = self._decode_slot(payload, offset)
            if item is not None:
                slots[slot_index] = item
        carried, _ = self._decode_slot(payload, offset)
        if window_id == 0:
            inventory = self.state["inventory"]
            if state_id >= inventory["state_id"]:
                inventory.update(
                    {"slots": slots, "carried": carried, "state_id": state_id}
                )

    def _handle_set_slot(self, payload):
        window_id = struct.unpack_from(">b", payload, 0)[0]
        state_id, consumed = Connection._decode_varint_bytes(payload, 1)
        offset = 1 + consumed
        slot_index = struct.unpack_from(">h", payload, offset)[0]
        item, _ = self._decode_slot(payload, offset + 2)
        inventory = self.state["inventory"]
        if window_id in (0, -2) and slot_index >= 0:
            if window_id == 0 and state_id < inventory["state_id"]:
                return
            if item is None:
                inventory["slots"].pop(slot_index, None)
            else:
                inventory["slots"][slot_index] = item
            inventory["state_id"] = state_id
        elif window_id == -1 and slot_index == -1:
            inventory["carried"] = item

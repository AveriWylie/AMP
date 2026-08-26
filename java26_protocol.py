"""Login and Configuration state handling for the Java 26 protocol generation."""

import struct
import time
import uuid

from chunk import Chunk
from entity_data import entity_name
from inventory_data import item_name
from protocol_data import packet_ids_for_protocol
from protocol_types import (
    BlockChanged, ChunkLoaded, EntitiesRemoved, EntityMoved, EntitySpawned,
    EntityTeleported, HealthChanged, PositionChanged, SelfEntityIdentified,
    HotbarSelected, InventoryReplaced, SlotChanged,
    ChatAction, EncodedAction, LookAction, MoveAction, PacketStep, SneakAction,
    SwingAction,
)


class Java26ProtocolAdapter:
    def __init__(self, family, version, connection):
        self.family = family
        self.version = version
        self.connection = connection
        protocol = connection._protocol_version
        self.login_clientbound = packet_ids_for_protocol(protocol, "clientbound", "login")
        self.login_serverbound = packet_ids_for_protocol(protocol, "serverbound", "login")
        self.configuration_clientbound = packet_ids_for_protocol(
            protocol, "clientbound", "configuration"
        )
        self.configuration_serverbound = packet_ids_for_protocol(
            protocol, "serverbound", "configuration"
        )
        self.play_clientbound = packet_ids_for_protocol(protocol, "clientbound")
        self.play_serverbound = packet_ids_for_protocol(protocol, "serverbound")

    def _packet(self, name, data):
        packet_id = self.connection._encode_varint(self.play_serverbound[name])
        return self.connection._encode_varint(len(packet_id + data)) + packet_id + data

    def encode_action(self, action, world_state, game_mode):
        encode = self.connection._encode_varint
        if isinstance(action, MoveAction):
            packet = self._packet("position", struct.pack(">dddB", action.x, action.y, action.z, 1))
        elif isinstance(action, LookAction):
            packet = self._packet("look", struct.pack(">ffB", action.yaw, action.pitch, 1))
        elif isinstance(action, ChatAction):
            message = action.message.encode("utf-8")
            data = (encode(len(message)) + message + struct.pack(">q", int(time.time() * 1000))
                    + struct.pack(">q", 0) + b"\x00" + encode(0) + b"\x00" * 3 + b"\x00")
            packet = self._packet("chat_message", data)
        elif isinstance(action, SwingAction):
            packet = self._packet("arm_animation", encode(action.hand))
        elif isinstance(action, SneakAction):
            packet = self._packet("player_input", b"\x20" if action.sneaking else b"\x00")
        else:
            raise TypeError(f"Unsupported Java 26 action: {type(action).__name__}")
        return EncodedAction((PacketStep(packet),))

    def decode_play(self, packet_id, payload):
        ids = self.play_clientbound
        if packet_id == ids["position"]:
            teleport_id, offset = self.connection._decode_varint_bytes(payload, 0)
            x, y, z, dx, dy, dz, yaw, pitch, flags = struct.unpack_from(
                ">ddddddffI", payload, offset
            )
            self.connection._send_protocol_packet(
                self.play_serverbound["teleport_confirm"],
                self.connection._encode_varint(teleport_id),
            )
            return [PositionChanged(x, y, z, yaw, pitch, flags)]
        if packet_id == ids["update_health"]:
            health = struct.unpack_from(">f", payload, 0)[0]
            food, consumed = self.connection._decode_varint_bytes(payload, 4)
            saturation = struct.unpack_from(">f", payload, 4 + consumed)[0]
            return [HealthChanged(health, food, saturation)]
        if packet_id == ids["block_change"]:
            packed = struct.unpack_from(">q", payload, 0)[0]
            x = packed >> 38
            z = (packed >> 12) & 0x3FFFFFF
            y = packed & 0xFFF
            if z >= 1 << 25:
                z -= 1 << 26
            if y >= 1 << 11:
                y -= 1 << 12
            state_id, _ = self.connection._decode_varint_bytes(payload, 8)
            return [BlockChanged(x, y, z, state_id)]
        if packet_id == ids["map_chunk"]:
            return [self._decode_chunk(payload)]
        if packet_id == ids["login"]:
            return [SelfEntityIdentified(struct.unpack_from(">i", payload, 0)[0])]
        if packet_id == ids["spawn_entity"]:
            entity_id, consumed = self.connection._decode_varint_bytes(payload, 0)
            entity_uuid = str(uuid.UUID(bytes=payload[consumed:consumed + 16]))
            entity_type, type_size = self.connection._decode_varint_bytes(payload, consumed + 16)
            x, y, z = struct.unpack_from(">ddd", payload, consumed + 16 + type_size)
            return [EntitySpawned(
                entity_id, entity_uuid, entity_type,
                entity_name(self.version, entity_type), x, y, z,
            )]
        if packet_id in (ids["rel_entity_move"], ids["entity_move_look"]):
            entity_id, consumed = self.connection._decode_varint_bytes(payload, 0)
            dx, dy, dz = struct.unpack_from(">hhh", payload, consumed)
            return [EntityMoved(entity_id, dx / 4096, dy / 4096, dz / 4096)]
        if packet_id in (ids["entity_teleport"], ids["sync_entity_position"]):
            entity_id, consumed = self.connection._decode_varint_bytes(payload, 0)
            x, y, z = struct.unpack_from(">ddd", payload, consumed)
            return [EntityTeleported(entity_id, x, y, z)]
        if packet_id == ids["entity_destroy"]:
            count, consumed = self.connection._decode_varint_bytes(payload, 0)
            offset = consumed
            entity_ids = []
            for _ in range(count):
                entity_id, consumed = self.connection._decode_varint_bytes(payload, offset)
                offset += consumed
                entity_ids.append(entity_id)
            return [EntitiesRemoved(tuple(entity_ids))]
        if packet_id == ids["window_items"]:
            return [self._decode_window_items(payload)]
        if packet_id == ids["set_slot"]:
            return [self._decode_set_slot(payload)]
        if packet_id == ids["set_player_inventory"]:
            slot, consumed = self.connection._decode_varint_bytes(payload, 0)
            item, _ = self._decode_slot(payload, consumed)
            return [SlotChanged(0, None, slot, item)]
        if packet_id == ids["set_cursor_item"]:
            item, _ = self._decode_slot(payload, 0)
            return [SlotChanged(-1, None, -1, item)]
        if packet_id == ids["held_item_slot"]:
            slot, consumed = self.connection._decode_varint_bytes(payload, 0)
            if consumed != len(payload) or slot not in range(9):
                raise ConnectionError("Malformed selected-hotbar packet")
            return [HotbarSelected(slot)]
        return []

    def _decode_slot(self, payload, offset):
        start = offset
        count, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed
        if count == 0:
            return None, offset
        item_id, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed
        added, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed
        removed, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed
        components = {}
        for _ in range(added):
            component_type, consumed = self.connection._decode_varint_bytes(payload, offset)
            offset += consumed
            if component_type in (1, 2, 3, 12, 19, 31, 41, 43, 46, 48, 63):
                value, consumed = self.connection._decode_varint_bytes(payload, offset)
                offset += consumed
                components[component_type] = value
            elif component_type in (13, 42):
                length, consumed = self.connection._decode_varint_bytes(payload, offset)
                offset += consumed
                values = []
                for _ in range(length):
                    identifier, consumed = self.connection._decode_varint_bytes(payload, offset)
                    offset += consumed
                    level, consumed = self.connection._decode_varint_bytes(payload, offset)
                    offset += consumed
                    values.append((identifier, level))
                components[component_type] = values
            else:
                raise ConnectionError(
                    f"Unsupported Java 26 item component {component_type}"
                )
        removed_types = []
        for _ in range(removed):
            component_type, consumed = self.connection._decode_varint_bytes(payload, offset)
            offset += consumed
            removed_types.append(component_type)
        return {
            "id": item_id, "name": item_name(self.version, item_id), "count": count,
            "components": components, "removed_components": removed_types,
            "wire": payload[start:offset].hex(),
        }, offset

    def _decode_window_items(self, payload):
        window_id, consumed = self.connection._decode_varint_bytes(payload, 0)
        state_id, state_size = self.connection._decode_varint_bytes(payload, consumed)
        offset = consumed + state_size
        count, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed
        slots = []
        for slot_index in range(count):
            item, offset = self._decode_slot(payload, offset)
            if item is not None:
                slots.append((slot_index, item))
        carried, offset = self._decode_slot(payload, offset)
        if offset != len(payload):
            raise ConnectionError("Trailing bytes in Java 26 inventory packet")
        return InventoryReplaced(window_id, state_id, tuple(slots), carried)

    def _decode_set_slot(self, payload):
        window_id, consumed = self.connection._decode_varint_bytes(payload, 0)
        state_id, state_size = self.connection._decode_varint_bytes(payload, consumed)
        offset = consumed + state_size
        slot = struct.unpack_from(">h", payload, offset)[0]
        item, offset = self._decode_slot(payload, offset + 2)
        if offset != len(payload):
            raise ConnectionError("Trailing bytes in Java 26 slot packet")
        return SlotChanged(window_id, state_id, slot, item)

    def _decode_chunk(self, payload):
        chunk_x, chunk_z = struct.unpack_from(">ii", payload, 0)
        offset = 8
        heightmap_count, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed
        heightmap_names = {
            0: "WORLD_SURFACE_WG", 1: "WORLD_SURFACE", 2: "OCEAN_FLOOR_WG",
            3: "OCEAN_FLOOR", 4: "MOTION_BLOCKING", 5: "MOTION_BLOCKING_NO_LEAVES",
        }
        heightmaps = {}
        for _ in range(heightmap_count):
            kind, consumed = self.connection._decode_varint_bytes(payload, offset)
            offset += consumed
            count, consumed = self.connection._decode_varint_bytes(payload, offset)
            offset += consumed
            end = offset + count * 8
            if end > len(payload):
                raise ConnectionError("Truncated Java 26 heightmap")
            heightmaps[heightmap_names.get(kind, f"TYPE_{kind}")] = struct.unpack_from(
                f">{count}q", payload, offset
            )
            offset = end
        length, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed
        end = offset + length
        if end > len(payload):
            raise ConnectionError("Truncated Java 26 chunk data")
        return ChunkLoaded(
            chunk_x, chunk_z, Chunk(payload[offset:end], self.version, heightmaps=heightmaps)
        )

    def handle_login(self, packet_id, payload, session=None):
        ids = self.login_clientbound
        if packet_id == ids["compress"]:
            threshold, _ = self.connection._decode_varint_bytes(payload, 0)
            self.connection._compression_threshold = threshold
            return False
        if packet_id == ids["cookie_request"]:
            key, consumed = self._decode_string(payload)
            if consumed != len(payload):
                raise ConnectionError("Malformed Login cookie request")
            response = self.connection._encode_string(key) + b"\x00"
            self.connection._send_protocol_packet(
                self.login_serverbound["cookie_response"], response
            )
            return False
        if packet_id == ids["success"]:
            self.connection._send_protocol_packet(
                self.login_serverbound["login_acknowledged"]
            )
            self.handle_configuration()
            return True
        if packet_id == ids["disconnect"]:
            raise ConnectionError("Server disconnected during Login")
        if packet_id == ids["encryption_begin"]:
            raise ConnectionError("Server requires authenticated online-mode login")
        if packet_id == ids["login_plugin_request"]:
            raise ConnectionError("Unsupported Login plugin request")
        raise ConnectionError(f"Unexpected Login packet id {packet_id:#x}")

    def handle_configuration(self):
        self._send_settings()
        while True:
            packet_id, payload = self.connection._read_packet()
            ids = self.configuration_clientbound
            if packet_id == ids["finish_configuration"]:
                self.connection._send_protocol_packet(
                    self.configuration_serverbound["finish_configuration"]
                )
                return
            if packet_id == ids["keep_alive"]:
                self.connection._send_protocol_packet(
                    self.configuration_serverbound["keep_alive"], payload
                )
            elif packet_id == ids["ping"]:
                self.connection._send_protocol_packet(
                    self.configuration_serverbound["pong"], payload
                )
            elif packet_id == ids["cookie_request"]:
                key, _ = self._decode_string(payload)
                response = self.connection._encode_string(key) + b"\x00"
                self.connection._send_protocol_packet(
                    self.configuration_serverbound["cookie_response"], response
                )
            elif packet_id == ids["select_known_packs"]:
                self.connection._send_protocol_packet(
                    self.configuration_serverbound["select_known_packs"], b"\x00"
                )
            elif packet_id == ids["add_resource_pack"]:
                pack_id = payload[:16]
                if len(pack_id) != 16:
                    raise ConnectionError("Malformed resource-pack request")
                self.connection._send_protocol_packet(
                    self.configuration_serverbound["resource_pack_receive"],
                    pack_id + self.connection._encode_varint(1),
                )
            elif packet_id == ids["code_of_conduct"]:
                self.connection._send_protocol_packet(
                    self.configuration_serverbound["accept_code_of_conduct"]
                )
            elif packet_id == ids["disconnect"]:
                raise ConnectionError("Server disconnected during Configuration")
            elif packet_id == ids["transfer"]:
                raise ConnectionError("Server transfer requested during Configuration")

    def _send_settings(self):
        payload = (
            self.connection._encode_string("en_us")
            + struct.pack(">b", 10)
            + self.connection._encode_varint(0)
            + b"\x01\x7f"
            + self.connection._encode_varint(1)
            + b"\x00\x01"
            + self.connection._encode_varint(0)
        )
        self.connection._send_protocol_packet(
            self.configuration_serverbound["settings"], payload
        )

    def _decode_string(self, payload):
        length, consumed = self.connection._decode_varint_bytes(payload, 0)
        end = consumed + length
        if end > len(payload):
            raise ConnectionError("Truncated protocol string")
        return payload[consumed:end].decode("utf-8"), end

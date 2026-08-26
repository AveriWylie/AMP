"""Login and Configuration state handling for the Java 26 protocol generation."""

import struct
from chunk import Chunk
from protocol_data import packet_ids_for_protocol
from protocol_types import BlockChanged, ChunkLoaded, HealthChanged, PositionChanged


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
        return []

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

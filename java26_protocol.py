"""Login and Configuration state handling for the Java 26 protocol generation."""

import struct

from protocol_data import packet_ids_for_protocol


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

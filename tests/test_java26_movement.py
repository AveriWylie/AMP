import struct

from amp.connection import Connection
from amp.java26_protocol import Java26ProtocolAdapter
from amp.protocol_types import ChatAction, LookAction, MoveAction, SneakAction, SwingAction


def adapter():
    connection = Connection("localhost", 25565, "26.1", "AMP", None, 775)
    return Java26ProtocolAdapter("java-26.1", "26.1", connection)


def packet_body(packet):
    length, consumed = Connection._decode_varint_bytes(packet, 0)
    body = packet[consumed:]
    assert len(body) == length
    packet_id, consumed = Connection._decode_varint_bytes(body, 0)
    return packet_id, body[consumed:]


def encode(value):
    protocol = adapter()
    packet = protocol.encode_action(value, {}, "survival").steps[0].packet
    return protocol, packet_body(packet)


def test_java26_movement_uses_flag_byte():
    protocol, (packet_id, payload) = encode(MoveAction(1.5, 64, -2))
    assert packet_id == protocol.play_serverbound["position"]
    assert struct.unpack(">dddB", payload) == (1.5, 64, -2, 1)


def test_java26_movement_is_throttled_to_walking_rate():
    encoded = adapter().encode_action(MoveAction(1, 64, 2), {}, "survival")

    assert encoded.steps[0].delay_before >= 0.2


def test_java26_look_swing_and_sneak_use_current_schemas():
    protocol, (packet_id, payload) = encode(LookAction(90, -10))
    assert packet_id == protocol.play_serverbound["look"]
    assert struct.unpack(">ffB", payload) == (90, -10, 1)

    protocol, (packet_id, payload) = encode(SwingAction(1))
    assert (packet_id, payload) == (protocol.play_serverbound["arm_animation"], b"\x01")

    protocol, (packet_id, payload) = encode(SneakAction(True))
    assert (packet_id, payload) == (protocol.play_serverbound["player_input"], b"\x20")


def test_java26_unsigned_chat_ends_with_checksum_byte():
    protocol, (packet_id, payload) = encode(ChatAction("hello"))
    assert packet_id == protocol.play_serverbound["chat_message"]
    assert payload.startswith(b"\x05hello")
    assert payload[-5:] == b"\x00\x00\x00\x00\x00"

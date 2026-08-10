"""Byte-level tests for protocol 762 serverbound Play packets."""

import struct

from bot import Connection
from execution import Execute


def _executor():
    connection = Connection("localhost", 25565, "1.19.4", "TestBot", None, 762)
    return Execute(connection, "survival", "passive")


def _packet_body(packet):
    length, consumed = Connection._decode_varint_bytes(packet, 0)
    body = packet[consumed:]
    assert len(body) == length
    packet_id, consumed = Connection._decode_varint_bytes(body, 0)
    return packet_id, body[consumed:]


def _read_varint(data, offset=0):
    value, consumed = Connection._decode_varint_bytes(data, offset)
    return value, offset + consumed


def test_movement_packet_uses_position_id_and_schema():
    packet_id, data = _packet_body(_executor()._create_movement_packet(1.5, 64.0, -2.25))
    assert packet_id == 0x14
    assert struct.unpack(">ddd?", data) == (1.5, 64.0, -2.25, True)


def test_look_packet_uses_rotation_id_and_schema():
    packet_id, data = _packet_body(_executor()._create_look_packet(90.0, -15.5))
    assert packet_id == 0x16
    assert struct.unpack(">ff?", data) == (90.0, -15.5, True)


def test_entity_action_packet_uses_player_command_id():
    packet_id, data = _packet_body(_executor()._create_entity_action_packet(3, entity_id=300))
    entity_id, offset = _read_varint(data)
    action_id, offset = _read_varint(data, offset)
    jump_boost, offset = _read_varint(data, offset)
    assert packet_id == 0x1E
    assert (entity_id, action_id, jump_boost) == (300, 3, 0)
    assert offset == len(data)


def test_digging_packet_supports_negative_positions_and_sequence():
    executor = _executor()
    packet_id, data = _packet_body(executor._create_digging_packet(0, -1, -64, -2, face=5))
    status, offset = _read_varint(data)
    packed = struct.unpack_from(">Q", data, offset)[0]
    offset += 8
    face = struct.unpack_from(">b", data, offset)[0]
    sequence, offset = _read_varint(data, offset + 1)

    expected = ((-1 & 0x3FFFFFF) << 38) | ((-2 & 0x3FFFFFF) << 12) | (-64 & 0xFFF)
    assert packet_id == 0x1D
    assert (status, packed, face, sequence) == (0, expected, 5, 0)
    assert offset == len(data)


def test_place_packet_contains_interaction_sequence():
    executor = _executor()
    packet_id, data = _packet_body(executor._create_place_packet(-1, 64, -2, face=1, hand=0))
    hand, offset = _read_varint(data)
    packed = struct.unpack_from(">Q", data, offset)[0]
    offset += 8
    face, offset = _read_varint(data, offset)
    cursor = struct.unpack_from(">fff", data, offset)
    offset += 12
    inside_block = data[offset]
    sequence, offset = _read_varint(data, offset + 1)

    expected = ((-1 & 0x3FFFFFF) << 38) | ((-2 & 0x3FFFFFF) << 12) | 64
    assert packet_id == 0x31
    assert (hand, packed, face) == (0, expected, 1)
    assert cursor == (0.5, 0.5, 0.5)
    assert inside_block == 0
    assert sequence == 0
    assert offset == len(data)


def test_interaction_sequence_increments_across_packets():
    executor = _executor()
    first_id, first_data = _packet_body(executor._create_use_item_packet(hand=0))
    second_id, second_data = _packet_body(executor._create_use_item_packet(hand=1))

    first_hand, offset = _read_varint(first_data)
    first_sequence, first_end = _read_varint(first_data, offset)
    second_hand, offset = _read_varint(second_data)
    second_sequence, second_end = _read_varint(second_data, offset)

    assert first_id == second_id == 0x32
    assert (first_hand, first_sequence) == (0, 0)
    assert (second_hand, second_sequence) == (1, 1)
    assert first_end == len(first_data)
    assert second_end == len(second_data)


def test_protocol_762_packet_id_table():
    assert Execute.play_ids == {
        "chat": 0x05,
        "position": 0x14,
        "look": 0x16,
        "block_dig": 0x1D,
        "entity_action": 0x1E,
        "swing": 0x2F,
        "block_place": 0x31,
        "use_item": 0x32,
    }

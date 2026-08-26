"""Byte-level tests for protocol 762 serverbound Play packets."""

import struct

from bot import Connection
from execution import Execute


def _executor():
    connection = Connection("localhost", 25565, "1.19.4", "TestBot", None, 762)
    return Execute(connection, "survival", "passive")


def _executor_1202(world_state=None):
    connection = Connection("localhost", 25565, "1.20.2", "TestBot", None, 764)
    return Execute(connection, "survival", "passive", world_state=world_state)


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


def test_1202_movement_packet_uses_generated_id_and_unchanged_schema():
    packet_id, data = _packet_body(
        _executor_1202()._create_movement_packet(1.5, 64.0, -2.25)
    )
    assert packet_id == 0x16
    assert struct.unpack(">ddd?", data) == (1.5, 64.0, -2.25, True)


def test_execute_queue_sends_one_movement_per_tick_and_updates_position():
    world_state = {
        "position": {"x": 0.0, "y": 64.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0}
    }
    executor = _executor_1202(world_state)
    sent = []
    executor._connection._send = sent.append
    executor.enque_command({"action": "move", "x": 1.0, "y": 64.0, "z": 0.0})
    executor.enque_command({"action": "move", "x": 2.0, "y": 64.0, "z": 0.0})

    executor.execute_queue()
    assert len(sent) == 1
    assert len(executor._command_queue) == 1
    assert world_state["position"]["x"] == 1.0

    executor.execute_queue()
    assert len(sent) == 2
    assert not executor._command_queue
    assert world_state["position"]["x"] == 2.0


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


def test_creative_mining_sends_only_start_digging():
    executor = _executor_1202()
    executor._game_mode = "creative"
    sent = []
    executor._connection._send = sent.append

    executor._execute({"action": "mine", "x": 1, "y": 64, "z": 2, "face": 2})

    assert len(sent) == 1
    packet_id, data = _packet_body(sent[0])
    status, _ = _read_varint(data)
    assert packet_id == executor.play_ids["block_dig"]
    assert status == 0


def test_survival_mining_waits_between_start_and_finish(monkeypatch):
    executor = _executor_1202()
    sent = []
    waits = []
    executor._connection._send = sent.append
    monkeypatch.setattr("execution.time.sleep", waits.append)

    executor._execute({
        "action": "mine", "x": 1, "y": 64, "z": 2, "face": 2, "duration": 0.3
    })

    assert len(sent) == 2
    assert waits == [0.3]
    statuses = [_read_varint(_packet_body(packet)[1])[0] for packet in sent]
    assert statuses == [0, 2]


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


def test_1202_hotbar_selection_packet_and_validation():
    executor = _executor_1202()
    packet_id, data = _packet_body(executor._create_held_item_packet(7))
    assert packet_id == 0x2B
    assert struct.unpack(">h", data)[0] == 7

    import pytest
    with pytest.raises(ValueError):
        executor._create_held_item_packet(9)


def test_1202_swaps_main_inventory_tool_into_selected_hotbar():
    world_state = {
        "inventory": {
            "state_id": 7,
            "selected_hotbar_slot": 2,
            "slots": {
                10: {"id": 799, "name": "diamond_pickaxe", "count": 1},
                38: {"id": 1, "name": "stone", "count": 12},
            },
        }
    }
    executor = _executor_1202(world_state)
    packet_id, data = _packet_body(executor._create_hotbar_swap_packet(10, 2))

    assert packet_id == 0x0D
    assert data[0] == 0  # player inventory window
    state_id, offset = _read_varint(data, 1)
    source_slot = struct.unpack_from(">h", data, offset)[0]
    button = struct.unpack_from(">b", data, offset + 2)[0]
    mode, _ = _read_varint(data, offset + 3)
    assert (state_id, source_slot, button, mode) == (7, 10, 2, 2)


def test_protocol_762_packet_id_table():
    assert _executor().play_ids == {
        "teleport_confirm": 0x00,
        "chat_message": 0x05,
        "client_command": 0x07,
        "keep_alive": 0x12,
        "position": 0x14,
        "look": 0x16,
        "block_dig": 0x1D,
        "entity_action": 0x1E,
        "arm_animation": 0x2F,
        "block_place": 0x31,
        "use_item": 0x32,
        "held_item_slot": 0x28,
        "window_click": 0x0B,
    }

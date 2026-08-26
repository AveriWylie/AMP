"""Byte-level tests for reference-adapter serverbound Play packets."""

import struct

import pytest

from connection import Connection
from execution import Execute
from legacy_protocol import LegacyProtocolAdapter
from protocol_data import packet_ids_for_protocol
from protocol_types import action_from_command


def _executor():
    return _executor_1202()


def _executor_1202(world_state=None):
    connection = Connection("localhost", 25565, "1.20.2", "TestBot", None, 764)
    adapter = LegacyProtocolAdapter(
        "1.20.2", connection, packet_ids_for_protocol(764, "clientbound")
    )
    return Execute(connection, "survival", "passive", adapter, world_state=world_state)


def _encode(executor, command):
    return executor._protocol_adapter.encode_action(
        action_from_command(command), executor._world_state, executor._game_mode
    ).steps


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
    packet_id, data = _packet_body(_encode(_executor(), {"action": "move", "x": 1.5, "y": 64.0, "z": -2.25})[0].packet)
    assert packet_id == 0x16
    assert struct.unpack(">ddd?", data) == (1.5, 64.0, -2.25, True)


def test_1202_movement_packet_uses_generated_id_and_unchanged_schema():
    packet_id, data = _packet_body(
        _encode(_executor_1202(), {"action": "move", "x": 1.5, "y": 64.0, "z": -2.25})[0].packet
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


def test_executor_rejects_unknown_action():
    with pytest.raises(ValueError, match="Unsupported action"):
        _executor()._execute({"action": "dance"})


def test_look_packet_uses_rotation_id_and_schema():
    packet_id, data = _packet_body(_encode(_executor(), {"action": "look", "yaw": 90.0, "pitch": -15.5})[0].packet)
    assert packet_id == 0x18
    assert struct.unpack(">ff?", data) == (90.0, -15.5, True)


def test_entity_action_packet_uses_player_command_id():
    executor = _executor_1202({"self_entity_id": 300})
    packet_id, data = _packet_body(_encode(executor, {"action": "sneak", "sneaking": True})[0].packet)
    entity_id, offset = _read_varint(data)
    action_id, offset = _read_varint(data, offset)
    jump_boost, offset = _read_varint(data, offset)
    assert packet_id == 0x21
    assert (entity_id, action_id, jump_boost) == (300, 0, 0)
    assert offset == len(data)


def test_attack_packet_uses_interact_entity_attack_schema():
    executor = _executor_1202()
    packet_id, data = _packet_body(_encode(executor, {"action": "attack", "entity_id": 300})[0].packet)
    entity_id, offset = _read_varint(data)
    interaction, offset = _read_varint(data, offset)
    assert packet_id == executor._protocol_adapter.serverbound_ids["use_entity"]
    assert (entity_id, interaction, data[offset]) == (300, 1, 0)
    assert offset + 1 == len(data)


def test_digging_packet_supports_negative_positions_and_sequence():
    executor = _executor()
    packet_id, data = _packet_body(_encode(executor, {"action": "mine", "x": -1, "y": -64, "z": -2, "face": 5})[0].packet)
    status, offset = _read_varint(data)
    packed = struct.unpack_from(">Q", data, offset)[0]
    offset += 8
    face = struct.unpack_from(">b", data, offset)[0]
    sequence, offset = _read_varint(data, offset + 1)

    expected = ((-1 & 0x3FFFFFF) << 38) | ((-2 & 0x3FFFFFF) << 12) | (-64 & 0xFFF)
    assert packet_id == 0x20
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
    assert packet_id == executor._protocol_adapter.serverbound_ids["block_dig"]
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
    packet_id, data = _packet_body(_encode(executor, {"action": "place", "x": -1, "y": 64, "z": -2, "face": 1})[0].packet)
    hand, offset = _read_varint(data)
    packed = struct.unpack_from(">Q", data, offset)[0]
    offset += 8
    face, offset = _read_varint(data, offset)
    cursor = struct.unpack_from(">fff", data, offset)
    offset += 12
    inside_block = data[offset]
    sequence, offset = _read_varint(data, offset + 1)

    expected = ((-1 & 0x3FFFFFF) << 38) | ((-2 & 0x3FFFFFF) << 12) | 64
    assert packet_id == 0x34
    assert (hand, packed, face) == (0, expected, 1)
    assert cursor == (0.5, 0.5, 0.5)
    assert inside_block == 0
    assert sequence == 0
    assert offset == len(data)


def test_interaction_sequence_increments_across_packets():
    executor = _executor()
    first_id, first_data = _packet_body(_encode(executor, {"action": "use_item", "hand": 0})[0].packet)
    second_id, second_data = _packet_body(_encode(executor, {"action": "use_item", "hand": 1})[0].packet)

    first_hand, offset = _read_varint(first_data)
    first_sequence, first_end = _read_varint(first_data, offset)
    second_hand, offset = _read_varint(second_data)
    second_sequence, second_end = _read_varint(second_data, offset)

    assert first_id == second_id == 0x35
    assert (first_hand, first_sequence) == (0, 0)
    assert (second_hand, second_sequence) == (1, 1)
    assert first_end == len(first_data)
    assert second_end == len(second_data)


def test_1202_hotbar_selection_packet_and_validation():
    executor = _executor_1202()
    packet_id, data = _packet_body(_encode(executor, {"action": "select_hotbar", "slot": 7})[0].packet)
    assert packet_id == 0x2B
    assert struct.unpack(">h", data)[0] == 7

    with pytest.raises(ValueError):
        _encode(executor, {"action": "select_hotbar", "slot": 9})


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
    packet_id, data = _packet_body(_encode(executor, {"action": "swap_hotbar", "source_slot": 10, "hotbar_slot": 2})[0].packet)

    assert packet_id == 0x0D
    assert data[0] == 0  # player inventory window
    state_id, offset = _read_varint(data, 1)
    source_slot = struct.unpack_from(">h", data, offset)[0]
    button = struct.unpack_from(">b", data, offset + 2)[0]
    mode, _ = _read_varint(data, offset + 3)
    assert (state_id, source_slot, button, mode) == (7, 10, 2, 2)


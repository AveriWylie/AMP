import struct

from connection import Connection
from java26_protocol import Java26ProtocolAdapter
from protocol_types import (
    AttackAction, MineAction, PlaceAction, SelectHotbarAction, SwapHotbarAction,
    UseItemAction,
)


def adapter():
    connection = Connection("localhost", 25565, "26.1", "AMP", None, 775)
    return Java26ProtocolAdapter("java-26.1", "26.1", connection)


def packet_body(packet):
    _, consumed = Connection._decode_varint_bytes(packet, 0)
    body = packet[consumed:]
    packet_id, consumed = Connection._decode_varint_bytes(body, 0)
    return packet_id, body[consumed:]


def test_java26_survival_mining_emits_start_and_delayed_finish():
    protocol = adapter()
    encoded = protocol.encode_action(MineAction(-1, 64, -2, 5, .25), {}, "survival")
    assert [step.delay_before for step in encoded.steps] == [0, .25]
    statuses = [Connection._decode_varint_bytes(packet_body(step.packet)[1], 0)[0]
                for step in encoded.steps]
    assert statuses == [0, 2]
    assert all(packet_body(step.packet)[0] == protocol.play_serverbound["block_dig"]
               for step in encoded.steps)


def test_java26_place_and_use_item_include_new_fields():
    protocol = adapter()
    place = protocol.encode_action(PlaceAction(1, 64, 2, 1), {}, "survival")
    packet_id, payload = packet_body(place.steps[0].packet)
    assert packet_id == protocol.play_serverbound["block_place"]
    assert payload[-3:-1] == b"\x00\x00"  # inside-block and world-border-hit

    use = protocol.encode_action(
        UseItemAction(1), {"position": {"yaw": 30, "pitch": -5}}, "survival"
    )
    packet_id, payload = packet_body(use.steps[0].packet)
    assert packet_id == protocol.play_serverbound["use_item"]
    assert struct.unpack(">ff", payload[-8:]) == (30, -5)


def test_java26_attack_and_hotbar_selection_use_dedicated_packets():
    protocol = adapter()
    attack = protocol.encode_action(AttackAction(300), {}, "survival")
    packet_id, payload = packet_body(attack.steps[0].packet)
    assert packet_id == protocol.play_serverbound["attack"]
    assert Connection._decode_varint_bytes(payload, 0)[0] == 300

    selected = protocol.encode_action(SelectHotbarAction(7), {}, "survival")
    packet_id, payload = packet_body(selected.steps[0].packet)
    assert packet_id == protocol.play_serverbound["held_item_slot"]
    assert struct.unpack(">h", payload)[0] == 7


def test_java26_hotbar_swap_uses_hashed_slots_for_plain_items():
    protocol = adapter()
    world = {"inventory": {"state_id": 4, "slots": {
        10: {"id": 2, "count": 1, "components": {}, "removed_components": []},
        38: {"id": 3, "count": 8, "components": {}, "removed_components": []},
    }}}
    encoded = protocol.encode_action(SwapHotbarAction(10, 2), world, "survival")
    packet_id, payload = packet_body(encoded.steps[0].packet)
    assert packet_id == protocol.play_serverbound["window_click"]
    assert payload.startswith(b"\x00\x04" + struct.pack(">hb", 10, 2) + b"\x02\x02")


def test_java26_hotbar_swap_rejects_modified_items_without_component_hashes():
    protocol = adapter()
    world = {"inventory": {"state_id": 0, "slots": {
        10: {"id": 2, "count": 1, "components": {3: 5}, "removed_components": []},
    }}}
    try:
        protocol.encode_action(SwapHotbarAction(10, 0), world, "survival")
        assert False
    except ValueError as error:
        assert "Cannot hash modified item" in str(error)

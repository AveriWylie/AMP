import struct

from amp.connection import Connection
from amp.java26_protocol import Java26ProtocolAdapter
from amp.world_state import WorldStateTracker


def setup_inventory():
    connection = Connection("localhost", 25565, "26.1", "AMP", None, 775)
    adapter = Java26ProtocolAdapter("java-26.1", "26.1", connection)
    return adapter, WorldStateTracker(adapter, connection)


def stack(item_id=1, count=1, damage=None):
    result = Connection._encode_varint(count) + Connection._encode_varint(item_id)
    if damage is None:
        return result + b"\x00\x00"
    return result + b"\x01\x00\x03" + Connection._encode_varint(damage)


def test_java26_inventory_decodes_component_item_stacks():
    adapter, tracker = setup_inventory()
    payload = b"\x00\x07\x02" + stack(1, 12) + stack(2, 1, damage=5) + b"\x00"

    tracker._on_packet(adapter.play_clientbound["window_items"], payload)

    inventory = tracker.state["inventory"]
    assert inventory["state_id"] == 7
    assert inventory["slots"][0]["count"] == 12
    assert inventory["slots"][1]["components"] == {3: 5}


def test_java26_inventory_handles_direct_slot_cursor_and_hotbar_packets():
    adapter, tracker = setup_inventory()
    tracker.state["inventory"]["state_id"] = 9

    tracker._on_packet(
        adapter.play_clientbound["set_player_inventory"], b"\x05" + stack(3, 2)
    )
    tracker._on_packet(adapter.play_clientbound["set_cursor_item"], stack(4, 1))
    tracker._on_packet(adapter.play_clientbound["held_item_slot"], b"\x08")

    inventory = tracker.state["inventory"]
    assert inventory["slots"][41]["count"] == 2
    assert inventory["carried"]["id"] == 4
    assert inventory["selected_hotbar_slot"] == 8
    assert inventory["state_id"] == 9


def test_java26_inventory_maps_player_slots_to_container_slots():
    adapter, tracker = setup_inventory()

    for player_slot in (0, 8, 9, 35, 36, 39, 40):
        tracker._on_packet(
            adapter.play_clientbound["set_player_inventory"],
            Connection._encode_varint(player_slot) + stack(player_slot + 1),
        )

    slots = tracker.state["inventory"]["slots"]
    assert {slot: item["id"] for slot, item in slots.items()} == {
        36: 1, 44: 9, 9: 10, 35: 36,
        8: 37, 5: 40, 45: 41,
    }


def test_java26_inventory_rejects_unknown_component_without_desynchronizing():
    adapter, _ = setup_inventory()
    unsupported = b"\x01\x01\x01\x00\x50"

    try:
        adapter.decode_play(adapter.play_clientbound["set_cursor_item"], unsupported)
        assert False
    except ConnectionError as error:
        assert "item component 80" in str(error)

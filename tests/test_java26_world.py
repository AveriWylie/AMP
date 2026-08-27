import struct

from connection import Connection
from java26_protocol import Java26ProtocolAdapter
from world_state import WorldStateTracker


def setup_world():
    connection = Connection("localhost", 25565, "26.1", "AMP", None, 775)
    connection._send_protocol_packet = lambda *args: None
    adapter = Java26ProtocolAdapter("java-26.1", "26.1", connection)
    return adapter, WorldStateTracker(adapter, connection)


def test_java26_position_correction_applies_relative_flags_and_confirms():
    connection = Connection("localhost", 25565, "26.1", "AMP", None, 775)
    sent = []
    connection._send_protocol_packet = lambda packet_id, payload=b"": sent.append((packet_id, payload))
    adapter = Java26ProtocolAdapter("java-26.1", "26.1", connection)
    tracker = WorldStateTracker(adapter, connection)
    tracker.state["position"].update({"x": 10, "y": 64, "z": -2, "yaw": 30, "pitch": 5})
    payload = Connection._encode_varint(7) + struct.pack(">ddddddffI", 1, 2, 3, 0, 0, 0, 5, -1, 0b11011)

    tracker._on_packet(adapter.play_clientbound["position"], payload)

    assert tracker.state["position"] == {"x": 11, "y": 66, "z": 3, "yaw": 35, "pitch": 4}
    assert sent == [(adapter.play_serverbound["teleport_confirm"], b"\x07")]


def test_java26_health_and_block_updates_are_normalized():
    adapter, tracker = setup_world()
    health = struct.pack(">f", 12.5) + b"\x0e" + struct.pack(">f", 3.0)
    tracker._on_packet(adapter.play_clientbound["update_health"], health)

    packed = ((-1 & 0x3FFFFFF) << 38) | ((2 & 0x3FFFFFF) << 12) | (64 & 0xFFF)
    events = adapter.decode_play(
        adapter.play_clientbound["block_change"], struct.pack(">Q", packed) + b"\x05"
    )

    assert (tracker.state["health"], tracker.state["food"]) == (12.5, 14)
    assert events[0].position == (-1, 64, 2)
    assert events[0].state_id == 5


def test_java26_chunk_wrapper_decodes_section_data():
    adapter, tracker = setup_world()
    section = struct.pack(">hh", 0, 0) + b"\x00\x00\x00\x00"
    sections = section * 24
    payload = struct.pack(">ii", 2, -3) + b"\x00" + Connection._encode_varint(len(sections)) + sections

    tracker._on_packet(adapter.play_clientbound["map_chunk"], payload)

    chunk = tracker.state["map"][(2, -3)]
    assert len(chunk._sections) == 24
    assert chunk.get_block(32, -64, -48) == "air"


def test_java26_chunk_rejects_truncated_section_array():
    adapter, _ = setup_world()
    payload = struct.pack(">ii", 0, 0) + b"\x00\x05\x00"

    try:
        adapter.decode_play(adapter.play_clientbound["map_chunk"], payload)
        assert False
    except ConnectionError as error:
        assert "Truncated Java 26 chunk data" in str(error)

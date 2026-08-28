import struct

from amp.connection import Connection
from amp.java26_protocol import Java26ProtocolAdapter
from amp.protocol_types import BlockChanged, ChunkLoaded
from amp.world_state import WorldStateTracker


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


def test_java26_multi_block_updates_are_normalized():
    adapter, _ = setup_world()
    section_x, section_y, section_z = 4, 4, -8
    packed = (
        ((section_x & 0x3FFFFF) << 42)
        | ((section_z & 0x3FFFFF) << 20)
        | (section_y & 0xFFFFF)
    )
    record = (5 << 12) | (13 << 8) | (2 << 4) | 1
    payload = (
        struct.pack(">Q", packed) + b"\x01"
        + Connection._encode_varint(record)
    )

    events = adapter.decode_play(
        adapter.play_clientbound["multi_block_change"], payload
    )

    assert events == [BlockChanged(77, 65, -126, 5)]


def test_java26_death_respawn_preserves_same_dimension_world_state():
    adapter, tracker = setup_world()
    tracker.state["dimension_id"] = 0
    tracker.state["position_revision"] = 3
    tracker.state["entities"][42] = {"name": "pig"}
    chunk = object()
    tracker.state["map"][(0, 0)] = chunk
    tracker.state["blocks"][(1, 2, 3)] = "stone"
    tracker.state["inventory"]["slots"][36] = {"id": 1, "count": 1}

    tracker._on_packet(adapter.play_clientbound["respawn"], b"\x00")

    assert tracker.state["position_revision"] == 4
    assert tracker.state["entities"] == {}
    assert tracker.state["map"] == {(0, 0): chunk}
    assert tracker.state["blocks"] == {(1, 2, 3): "stone"}
    assert tracker.state["inventory"]["slots"] == {}


def test_java26_dimension_change_discards_world_state():
    adapter, tracker = setup_world()
    tracker.state["dimension_id"] = 0
    tracker.state["entities"][42] = {"name": "pig"}
    tracker.state["map"][(0, 0)] = object()
    tracker.state["blocks"][(1, 2, 3)] = "stone"

    tracker._on_packet(adapter.play_clientbound["respawn"], b"\x01")

    assert tracker.state["dimension_id"] == 1
    assert tracker.state["entities"] == {}
    assert tracker.state["map"] == {}
    assert tracker.state["blocks"] == {}


def test_java26_respawn_discards_stale_players_and_reports_loaded():
    adapter, tracker = setup_world()
    tracker.connection._send = lambda packet: None
    sent = []
    tracker.connection._send_protocol_packet = (
        lambda packet_id, payload=b"": sent.append((packet_id, payload))
    )
    tracker.state["dimension_id"] = 0
    player = {
        "uuid": "player-uuid", "type": 156, "name": "player",
        "x": 83.5, "y": 65.0, "z": -123.0,
    }
    tracker.state["entities"] = {
        105: player,
        131: {"uuid": "pig-uuid", "type": 100, "name": "pig",
              "x": 80.0, "y": 65.0, "z": -120.0},
    }
    health = struct.pack(">f", 0.0) + b"\x14" + struct.pack(">f", 0.0)

    tracker._on_packet(adapter.play_clientbound["update_health"], health)
    tracker._on_packet(
        adapter.play_clientbound["entity_destroy"], b"\x02\x69\x83\x01"
    )
    tracker._on_packet(adapter.play_clientbound["respawn"], b"\x00")
    position = (
        b"\x01" + struct.pack(">ddddddffI", 100, 69, -119, 0, 0, 0, 0, 0, 0)
    )
    tracker._on_packet(adapter.play_clientbound["position"], position)

    assert tracker.state["entities"] == {}
    assert sent[-1] != (adapter.play_serverbound["player_loaded"], b"")

    tracker.apply(ChunkLoaded(6, -8, object()))

    assert sent[-1] == (adapter.play_serverbound["player_loaded"], b"")


def test_java26_same_dimension_respawn_reuses_loaded_position_chunk():
    adapter, tracker = setup_world()
    tracker.connection._send = lambda packet: None
    sent = []
    tracker.connection._send_protocol_packet = (
        lambda packet_id, payload=b"": sent.append((packet_id, payload))
    )
    tracker.state["dimension_id"] = 0
    tracker.state["map"][(6, -8)] = object()

    tracker._on_packet(adapter.play_clientbound["respawn"], b"\x00")
    position = (
        b"\x01"
        + struct.pack(">ddddddffI", 100, 69, -119, 0, 0, 0, 0, 0, 0)
    )
    tracker._on_packet(adapter.play_clientbound["position"], position)

    assert sent[-1] == (adapter.play_serverbound["player_loaded"], b"")


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

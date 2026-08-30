import struct
import uuid

from amp.connection import Connection
from amp.java26_protocol import Java26ProtocolAdapter
from amp.world_state import WorldStateTracker


def setup_entities():
    connection = Connection("localhost", 25565, "26.1", "AMP", None, 775)
    adapter = Java26ProtocolAdapter("java-26.1", "26.1", connection)
    return adapter, WorldStateTracker(adapter, connection)


def test_entity_trace_reports_spawn_and_removal(monkeypatch, capsys):
    monkeypatch.setenv("AMP_TRACE_ENTITIES", "1")
    adapter, tracker = setup_entities()
    entity_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    spawn = (
        b"\x2a" + entity_uuid.bytes + b"\x9b\x01"
        + struct.pack(">ddd", 1, 64, -2)
    )

    tracker.on_packet(adapter.play_clientbound["spawn_entity"], spawn)
    tracker.on_packet(adapter.play_clientbound["entity_destroy"], b"\x01\x2a")

    output = capsys.readouterr().out
    assert "Entity trace: spawn id=42" in output
    assert "Entity trace: remove players={42:" in output


def test_java26_entity_lifecycle_updates_normalized_world_state():
    adapter, tracker = setup_entities()
    entity_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    spawn = b"\x2a" + entity_uuid.bytes + b"\x01" + struct.pack(">ddd", 1, 64, -2)

    tracker.on_packet(adapter.play_clientbound["spawn_entity"], spawn)
    tracker.on_packet(
        adapter.play_clientbound["rel_entity_move"],
        b"\x2a" + struct.pack(">hhh?", 4096, -2048, 8192, True),
    )
    assert (tracker.state["entities"][42]["x"], tracker.state["entities"][42]["y"]) == (2, 63.5)

    tracker.on_packet(
        adapter.play_clientbound["entity_teleport"],
        b"\x2a" + struct.pack(">ddd", 9, 70, 8),
    )
    assert tracker.state["entities"][42]["uuid"] == str(entity_uuid)
    assert tracker.state["entities"][42]["name"] != ""
    assert (tracker.state["entities"][42]["x"], tracker.state["entities"][42]["y"]) == (9, 70)

    tracker.on_packet(adapter.play_clientbound["entity_destroy"], b"\x01\x2a")
    assert tracker.state["entities"] == {}


def test_java26_play_login_identifies_the_bot_entity():
    adapter, tracker = setup_entities()
    world = b"minecraft:overworld"
    payload = (
        struct.pack(">i?", 123, False)
        + b"\x01"
        + bytes((len(world),))
        + world
        + b"\x14\x08\x08"
        + b"\x00\x01\x00"
        + b"\x02"
    )
    tracker.on_packet(adapter.play_clientbound["login"], payload)
    assert tracker.state["self_entity_id"] == 123
    assert tracker.state["dimension_id"] == 2


def test_java26_sync_entity_position_is_absolute():
    adapter, tracker = setup_entities()
    tracker.state["entities"][7] = {"x": 0, "y": 0, "z": 0}
    payload = b"\x07" + struct.pack(">ddddddff?", 3, 4, 5, 0, 0, 0, 0, 0, True)
    tracker.on_packet(adapter.play_clientbound["sync_entity_position"], payload)
    assert tracker.state["entities"][7] == {"x": 3, "y": 4, "z": 5}

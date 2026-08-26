"""Tests for decoding clientbound Play packets into bot world state."""

import struct
import uuid

import pytest

from bot import Bot
from connection import Connection
from world_state import WorldStateTracker


def _bot():
    return Bot({
        "host": "localhost",
        "port": 25565,
        "username": "TestBot",
        "version": "1.19.4",
        "game_mode": "survival",
        "behavior_mode": "passive",
    })


def test_bot_delegates_packet_state_to_world_tracker():
    bot = _bot()

    assert isinstance(bot._world_tracker, WorldStateTracker)
    assert bot._world_state is bot._world_tracker.state
    assert bot._connection._packet_handler == bot._world_tracker._on_packet


def _packed_position(x, y, z):
    packed = ((x & 0x3FFFFFF) << 38) | ((z & 0x3FFFFFF) << 12) | (y & 0xFFF)
    return struct.pack(">Q", packed)


def test_decode_varint_bytes_tracks_multibyte_length_and_offset():
    value, consumed = Connection._decode_varint_bytes(b"prefix\xac\x02suffix", 6)
    assert value == 300
    assert consumed == 2


@pytest.mark.parametrize("payload", [
    b"",
    b"\x80",
    b"\x80\x80\x80\x80\x80\x00",
    b"\xff\xff\xff\xff\x10",
])
def test_decode_varint_bytes_rejects_invalid_input(payload):
    with pytest.raises(ValueError):
        Connection._decode_varint_bytes(payload, 0)


def test_position_confirmation_echoes_multibyte_teleport_id():
    bot = _bot()
    sent = []
    bot._connection._send = sent.append
    payload = struct.pack(">dddffB", 1.0, 64.0, -2.0, 90.0, 10.0, 0)
    payload += Connection._encode_varint(300)

    bot._world_tracker._handle_position(payload)

    assert bot._world_state["position"]["x"] == 1.0
    assert sent == [b"\x03\x00\xac\x02"]


def test_health_decodes_multibyte_food_varint():
    bot = _bot()
    payload = struct.pack(">f", 18.5) + Connection._encode_varint(300) + struct.pack(">f", 2.0)

    bot._world_tracker._handle_health(payload)

    assert bot._world_state["health"] == 18.5
    assert bot._world_state["food"] == 300


def test_spawn_entity_decodes_varints_uuid_and_coordinates():
    bot = _bot()
    entity_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    payload = Connection._encode_varint(300)
    payload += entity_uuid.bytes
    payload += Connection._encode_varint(200)
    payload += struct.pack(">ddd", 10.5, 64.0, -3.25)
    payload += b"\x00" * 10

    bot._world_tracker._handle_entity(payload)

    assert bot._world_state["entities"][300] == {
        "uuid": str(entity_uuid),
        "type": 200,
        "name": "entity_200",
        "x": 10.5,
        "y": 64.0,
        "z": -3.25,
    }


def test_block_change_decodes_multibyte_state_id_and_negative_coordinates():
    bot = _bot()

    class FakeChunk:
        _sections = {3: {}}

    bot._world_state["map"][(-1, -1)] = FakeChunk()
    payload = _packed_position(-1, -1, -2) + Connection._encode_varint(300)

    bot._world_tracker._handle_block_update(payload)

    section = bot._world_state["map"][(-1, -1)]._sections[3]
    assert section["patched"][(15, 15, 14)] == 300


def test_play_packet_ids_match_protocol_762():
    bot = _bot()
    assert bot.play_ids == {
        "spawn_entity": 0x01,
        "login": 0x28,
        "rel_entity_move": 0x2B,
        "entity_move_look": 0x2C,
        "entity_destroy": 0x3E,
        "entity_teleport": 0x68,
        "block_change": 0x0A,
        "keep_alive": 0x23,
        "map_chunk": 0x24,
        "position": 0x3C,
        "update_health": 0x57,
        "window_items": 0x12,
        "set_slot": 0x14,
        "held_item_slot": 0x4D,
    }
    assert bot._connection.play_ids["keep_alive"] == 0x12


def test_entity_motion_teleport_and_destroy_keep_state_current():
    bot = _bot()
    bot._world_state["entities"][300] = {
        "uuid": "test", "type": 118, "name": "zombie",
        "x": 1.0, "y": 64.0, "z": 2.0,
    }
    entity_id = Connection._encode_varint(300)

    bot._world_tracker._handle_entity_move(
        entity_id + struct.pack(">hhh?", 4096, -2048, 8192, True)
    )
    assert bot._world_state["entities"][300]["x"] == 2.0
    assert bot._world_state["entities"][300]["y"] == 63.5
    assert bot._world_state["entities"][300]["z"] == 4.0

    bot._world_tracker._handle_entity_teleport(
        entity_id + struct.pack(">dddbb?", 8.0, 70.0, -4.0, 0, 0, True)
    )
    assert bot._world_state["entities"][300]["x"] == 8.0
    bot._world_tracker._handle_entity_destroy(Connection._encode_varint(1) + entity_id)
    assert 300 not in bot._world_state["entities"]


def test_login_packet_records_own_entity_id():
    bot = _bot()
    bot._world_tracker._on_packet(bot.play_ids["login"], struct.pack(">i", 1234))
    assert bot._world_state["self_entity_id"] == 1234

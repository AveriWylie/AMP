"""Byte-level inventory snapshot, slot update, NBT, and hotbar tests."""

import struct

from bot import Bot, Connection
from inventory_data import item_name


def _bot():
    return Bot({
        "host": "localhost", "port": 25565, "username": "InventoryTest",
        "version": "1.20.2", "game_mode": "creative", "behavior_mode": "passive",
    })


def _slot(item_id=None, count=1, nbt=b"\x00"):
    if item_id is None:
        return b"\x00"
    return b"\x01" + Connection._encode_varint(item_id) + struct.pack(">b", count) + nbt


def test_window_items_replaces_player_inventory_and_names_items():
    bot = _bot()
    payload = b"\x00" + Connection._encode_varint(12) + Connection._encode_varint(3)
    payload += _slot(1, 32) + _slot() + _slot(799, 1) + _slot()

    bot._handle_window_items(payload)

    inventory = bot._world_state["inventory"]
    assert inventory["state_id"] == 12
    assert inventory["slots"][0]["name"] == "stone"
    assert inventory["slots"][0]["count"] == 32
    assert inventory["slots"][2]["name"] == "diamond_pickaxe"
    assert inventory["slots"][2]["count"] == 1
    assert inventory["carried"] is None


def test_set_slot_adds_and_removes_hotbar_item():
    bot = _bot()
    prefix = b"\x00" + Connection._encode_varint(3) + struct.pack(">h", 38)
    bot._handle_set_slot(prefix + _slot(799))
    assert bot._world_state["inventory"]["slots"][38]["name"] == "diamond_pickaxe"

    bot._handle_set_slot(prefix + _slot())
    assert 38 not in bot._world_state["inventory"]["slots"]


def test_stale_window_snapshot_does_not_overwrite_newer_slot_update():
    bot = _bot()
    update = b"\x00" + Connection._encode_varint(5) + struct.pack(">h", 38) + _slot(799)
    bot._handle_set_slot(update)
    stale = b"\x00" + Connection._encode_varint(4) + Connection._encode_varint(1)
    stale += _slot() + _slot()

    bot._handle_window_items(stale)

    assert bot._world_state["inventory"]["state_id"] == 5
    assert bot._world_state["inventory"]["slots"][38]["name"] == "diamond_pickaxe"


def test_slot_decoder_skips_compound_nbt_before_next_slot():
    bot = _bot()
    # Anonymous compound containing a named string, followed by TAG_End.
    nbt = b"\x0a\x08\x00\x03foo\x00\x03bar\x00"
    payload = _slot(1, nbt=nbt) + _slot(23, 4)

    first, offset = bot._decode_slot(payload, 0)
    second, offset = bot._decode_slot(payload, offset)

    assert first["name"] == "stone"
    assert {key: second[key] for key in ("id", "name", "count")} == {
        "id": 23, "name": "oak_planks", "count": 4
    }
    assert offset == len(payload)


def test_clientbound_held_slot_updates_selected_hotbar():
    bot = _bot()
    bot._on_packet(bot.play_ids["held_item_slot"], b"\x06")
    assert bot._world_state["inventory"]["selected_hotbar_slot"] == 6


def test_item_registry_resolves_known_and_unknown_ids():
    assert item_name("1.20.2", 799) == "diamond_pickaxe"
    assert item_name("1.20.2", 99999) == "unknown:99999"

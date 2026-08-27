"""Vanilla base mining-speed and hotbar tool selection tests."""

from amp.mining_data import mining_plan


def _inventory(*items):
    return {
        "slots": {36 + slot: item for slot, item in items},
        "selected_hotbar_slot": 0,
    }


def test_stone_selects_diamond_pickaxe_and_calculates_six_ticks():
    plan = mining_plan("26.1.2", "stone", _inventory(
        (2, {"id": 939, "name": "diamond_pickaxe", "count": 1})
    ))
    assert plan["hotbar_slot"] == 2
    assert plan["inventory_slot"] == 38
    assert plan["ticks"] == 6
    assert plan["seconds"] == 0.3


def test_required_harvest_tool_refuses_inadequate_hotbar():
    assert mining_plan("26.1.2", "obsidian", _inventory(
        (0, {"id": 934, "name": "iron_pickaxe", "count": 1})
    )) is None


def test_soft_block_uses_matching_tool_but_not_unrelated_item():
    shovel = {"id": 938, "name": "diamond_shovel", "count": 1}
    pickaxe = {"id": 939, "name": "diamond_pickaxe", "count": 1}
    assert mining_plan("26.1.2", "dirt", _inventory((1, shovel)))["ticks"] == 2
    assert mining_plan("26.1.2", "dirt", _inventory((1, pickaxe)))["inventory_slot"] is None


def test_best_tool_can_come_from_main_inventory():
    inventory = _inventory()
    inventory["slots"][10] = {"id": 939, "name": "diamond_pickaxe", "count": 1}
    inventory["selected_hotbar_slot"] = 4
    plan = mining_plan("26.1.2", "stone", inventory)
    assert plan["inventory_slot"] == 10
    assert plan["hotbar_slot"] == 4


def test_unbreakable_or_unknown_block_is_rejected():
    assert mining_plan("26.1.2", "bedrock", _inventory()) is None
    assert mining_plan("26.1.2", "not_a_block", _inventory()) is None

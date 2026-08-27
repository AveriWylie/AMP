"""Placement orchestration tests for inventory, approach, support, and facing."""

from bot import Bot


class PlacementChunk:
    def __init__(self, occupied=False):
        self.occupied = occupied

    def get_block(self, x, y, z):
        if self.occupied and (x, y, z) == (2, 64, 2):
            return "stone"
        return "stone" if y <= 63 else "air"


def _bot(chunk=None):
    bot = Bot({
        "host": "localhost", "port": 25565, "username": "PlaceTest",
        "version": "26.1.2", "game_mode": "survival", "behavior_mode": "passive",
    })
    bot._world_state["map"][(0, 0)] = chunk or PlacementChunk()
    bot._world_state["position"].update({"x": 2.0, "y": 64.0, "z": 0.0})
    return bot


def test_place_block_swaps_full_inventory_stack_and_clicks_support_top():
    bot = _bot()
    bot._world_state["inventory"]["selected_hotbar_slot"] = 3
    bot._world_state["inventory"]["slots"][10] = {
        "id": 36, "name": "oak_planks", "count": 16
    }

    assert bot.place_block((2, 64, 2), "oak_planks") is True

    commands = list(bot._executor._command_queue)
    assert {"action": "swap_hotbar", "source_slot": 10, "hotbar_slot": 3} in commands
    assert commands[-2]["action"] == "look"
    assert commands[-1] == {
        "action": "place", "x": 2, "y": 63, "z": 2, "face": 1,
        "target": (2, 64, 2), "block": "oak_planks",
    }


def test_place_block_uses_existing_hotbar_stack_without_swap():
    bot = _bot()
    bot._world_state["inventory"]["slots"][38] = {
        "id": 36, "name": "oak_planks", "count": 4
    }
    assert bot.place_block((2, 64, 2), "oak_planks") is True
    commands = list(bot._executor._command_queue)
    assert not any(command["action"] == "swap_hotbar" for command in commands)
    assert {"action": "select_hotbar", "slot": 2} in commands


def test_place_block_rejects_occupied_target_or_missing_item():
    occupied = _bot(PlacementChunk(occupied=True))
    occupied._world_state["inventory"]["slots"][36] = {
        "id": 36, "name": "oak_planks", "count": 1
    }
    assert occupied.place_block((2, 64, 2), "oak_planks") is False

    missing = _bot()
    assert missing.place_block((2, 64, 2), "oak_planks") is False

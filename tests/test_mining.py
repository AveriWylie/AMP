"""Tests for turning a requested block target into movement and digging commands."""

from bot import Bot
from gameplay import GameplayController


class FlatChunk:
    def get_block(self, x, y, z):
        if (x, y, z) == (2, 64, 2):
            return "stone"
        return "stone" if y <= 63 else "air"


def test_bot_delegates_gameplay_actions_to_controller():
    bot = Bot({
        "host": "localhost", "port": 25565, "username": "Miner",
        "version": "1.20.2", "game_mode": "creative", "behavior_mode": "passive",
    })

    assert isinstance(bot._gameplay, GameplayController)
    assert bot.move_to == bot._gameplay.move_to
    assert bot.mine_block == bot._gameplay.mine_block


def test_mine_block_selects_reachable_face_and_queues_dig_last():
    bot = Bot({
        "host": "localhost",
        "port": 25565,
        "username": "TestBot",
        "version": "1.20.2",
        "game_mode": "creative",
        "behavior_mode": "passive",
    })
    bot._world_state["map"][(0, 0)] = FlatChunk()
    bot._world_state["position"].update({"x": 2.0, "y": 64.0, "z": 0.0})

    assert bot.mine_block((2, 64, 2)) is True

    commands = list(bot._executor._command_queue)
    assert commands[-2]["action"] == "look"
    assert commands[-1] == {
        "action": "mine", "x": 2, "y": 64, "z": 2, "face": 2, "duration": 0
    }
    assert commands[-3] == {"action": "move", "x": 2, "y": 64, "z": 1}


def test_mine_block_rejects_target_without_reachable_standing_position():
    bot = Bot({})
    assert bot.mine_block((2, 64, 2)) is False
    assert not bot._executor._command_queue


def test_survival_mining_selects_suitable_hotbar_tool_and_duration():
    bot = Bot({
        "host": "localhost", "port": 25565, "username": "TestBot",
        "version": "1.20.2", "game_mode": "survival", "behavior_mode": "passive",
    })
    bot._world_state["map"][(0, 0)] = FlatChunk()
    bot._world_state["position"].update({"x": 2.0, "y": 64.0, "z": 0.0})
    bot._world_state["inventory"]["slots"][38] = {
        "id": 799, "name": "diamond_pickaxe", "count": 1
    }

    assert bot.mine_block((2, 64, 2)) is True
    commands = list(bot._executor._command_queue)
    assert {"action": "select_hotbar", "slot": 2} in commands
    assert commands[-1]["action"] == "mine"
    assert commands[-1]["duration"] == 0.3


def test_survival_mining_swaps_best_tool_from_main_inventory():
    bot = Bot({
        "host": "localhost", "port": 25565, "username": "TestBot",
        "version": "1.20.2", "game_mode": "survival", "behavior_mode": "passive",
    })
    bot._world_state["map"][(0, 0)] = FlatChunk()
    bot._world_state["position"].update({"x": 2.0, "y": 64.0, "z": 0.0})
    bot._world_state["inventory"]["selected_hotbar_slot"] = 4
    bot._world_state["inventory"]["slots"][10] = {
        "id": 799, "name": "diamond_pickaxe", "count": 1
    }

    assert bot.mine_block((2, 64, 2)) is True
    commands = list(bot._executor._command_queue)
    assert {"action": "swap_hotbar", "source_slot": 10, "hotbar_slot": 4} in commands
    assert not any(command["action"] == "select_hotbar" for command in commands)

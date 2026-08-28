"""Tests for turning a requested block target into movement and digging commands."""

from amp.bot import Bot
from amp.gameplay import GameplayController


class FlatChunk:
    def get_block(self, x, y, z):
        if (x, y, z) == (2, 64, 2):
            return "stone"
        return "stone" if y <= 63 else "air"


class CoveredTreeChunk:
    def get_block(self, x, y, z):
        if (x, y, z) == (2, 64, 0):
            return "dark_oak_log"
        if (x, y, z) == (2, 68, 0):
            return "dark_oak_leaves"
        return "stone" if y <= 63 else "air"


class RaisedGoalChunk:
    def get_block(self, x, y, z):
        if x == 3 and y == 64:
            return "stone"
        return "stone" if y <= 63 else "air"


def test_bot_delegates_gameplay_actions_to_controller():
    bot = Bot({
        "host": "localhost", "port": 25565, "username": "Miner",
        "version": "26.1.2", "game_mode": "creative",
    })

    assert isinstance(bot._gameplay, GameplayController)
    bot._gameplay.move_to = lambda goal: ("move", goal)
    bot._gameplay.mine_block = lambda target: ("mine", target)
    bot._gameplay.mine_nearest = lambda block, radius: (
        "mine_nearest", block, radius
    )
    bot._gameplay.place_block = lambda target, block: ("place", target, block)
    bot._gameplay.attack_entity = lambda entity_id: ("attack", entity_id)

    assert bot.move_to((1, 2, 3)) == ("move", (1, 2, 3))
    assert bot.mine_block((4, 5, 6)) == ("mine", (4, 5, 6))
    assert bot.mine_nearest("log", 8) == ("mine_nearest", "log", 8)
    assert bot.place_block((7, 8, 9), "stone") == ("place", (7, 8, 9), "stone")
    assert bot.attack_entity(42) == ("attack", 42)


def test_path_moves_skip_start_and_target_block_centers():
    bot = Bot({"version": "26.1.2", "game_mode": "creative"})
    bot._world_state["position"].update({
        "x": 55.63, "y": 68.0, "z": -135.63
    })
    bot._pathfinder.find_path_near = lambda *args, **kwargs: [
        (55, 68, -136),
        (54, 68, -136),
    ]

    assert bot.move_to((54, 68, -136)) is True

    commands = list(bot._executor._command_queue)
    assert len(commands) == 5
    assert commands[-1]["x"] == 54.5
    assert commands[-1]["z"] == -135.5
    assert all(command["y"] == 68 for command in commands)
    assert all(command["delay"] == 0.05 for command in commands)
    assert all(
        abs(current["x"] - previous["x"]) <= 0.200001
        for previous, current in zip(commands, commands[1:])
    )


def test_move_to_uses_nearby_walkable_height_when_exact_goal_is_blocked():
    bot = Bot({"version": "26.1.2", "game_mode": "creative"})
    bot._world_state["map"][(0, 0)] = RaisedGoalChunk()
    bot._world_state["position"].update({"x": 0.5, "y": 64, "z": 0.5})

    assert bot.move_to((3, 64, 0)) is True

    commands = list(bot._executor._command_queue)
    assert commands[-1] == {
        "action": "move", "x": 3.5, "y": 65, "z": 0.5,
        "on_ground": True, "delay": 0.05,
    }


def test_step_up_uses_airborne_intermediate_positions():
    bot = Bot({"version": "26.2", "game_mode": "creative"})
    bot._pathfinder.find_path_near = lambda *args, **kwargs: [
        (0, 64, 0),
        (1, 65, 0),
    ]

    assert bot.move_to((1, 65, 0)) is True

    commands = list(bot._executor._command_queue)
    assert len(commands) > 2
    assert commands[0]["y"] > 64
    assert commands[0]["on_ground"] is False
    assert all(command["delay"] == 0.05 for command in commands)
    assert all(
        command["y"] >= 65
        for command in commands
        if command["x"] + 0.3 > 1
    )
    assert commands[-1] == {
        "action": "move", "x": 1.5, "y": 65, "z": 0.5,
        "on_ground": True, "delay": 0.05,
    }


def test_step_down_uses_airborne_intermediate_positions():
    bot = Bot({"version": "26.2", "game_mode": "creative"})
    bot._pathfinder.find_path_near = lambda *args, **kwargs: [
        (0, 65, 0),
        (1, 64, 0),
    ]

    assert bot.move_to((1, 64, 0)) is True

    commands = list(bot._executor._command_queue)
    assert len(commands) > 2
    assert commands[0]["on_ground"] is True
    assert all(
        command["x"] - 0.3 >= 1
        for command in commands
        if command["y"] < 65
    )
    assert commands[-1] == {
        "action": "move", "x": 1.5, "y": 64, "z": 0.5,
        "on_ground": True, "delay": 0.05,
    }


def test_idle_physics_falls_after_server_teleport_into_air():
    bot = Bot({"version": "26.2", "game_mode": "survival"})
    bot._world_state["map"][(0, 0)] = FlatChunk()
    bot._world_state["position"].update({"x": 0.5, "y": 66, "z": 0.5})
    bot._world_state["position_revision"] = 4

    assert bot._gameplay.tick() is True

    command = bot._executor._command_queue[-1]
    assert command["x"] == 0.5
    assert command["y"] == 65.9216
    assert command["z"] == 0.5
    assert command["on_ground"] is False


def test_idle_physics_does_nothing_while_grounded():
    bot = Bot({"version": "26.2", "game_mode": "survival"})
    bot._world_state["map"][(0, 0)] = FlatChunk()
    bot._world_state["position"].update({"x": 0.5, "y": 64, "z": 0.5})

    assert bot._gameplay.tick() is False
    assert not bot._executor._command_queue


def test_idle_physics_waits_for_the_current_chunk():
    bot = Bot({"version": "26.2", "game_mode": "survival"})
    bot._world_state["position"].update({"x": 0.5, "y": 66, "z": 0.5})

    assert bot._gameplay.tick() is False
    assert not bot._executor._command_queue


def test_mine_block_selects_reachable_face_and_queues_dig_last():
    bot = Bot({
        "host": "localhost",
        "port": 25565,
        "username": "TestBot",
        "version": "26.1.2",
        "game_mode": "creative",
    })
    bot._world_state["map"][(0, 0)] = FlatChunk()
    bot._world_state["position"].update({"x": 2.0, "y": 64.0, "z": 0.0})

    assert bot.mine_block((2, 64, 2)) is True

    commands = list(bot._executor._command_queue)
    assert commands[-2]["action"] == "look"
    assert commands[-1] == {
        "action": "mine", "x": 2, "y": 64, "z": 2, "face": 2, "duration": 0
    }
    assert commands[-3]["action"] == "move"
    assert commands[-3]["x"] == 2.5
    assert commands[-3]["y"] == 64
    assert commands[-3]["z"] == 1.5


def test_mine_nearest_finds_log_hidden_below_leaf_canopy():
    bot = Bot({
        "host": "localhost",
        "port": 25565,
        "username": "TestBot",
        "version": "26.1.2",
        "game_mode": "creative",
    })
    bot._world_state["map"][(0, 0)] = CoveredTreeChunk()
    bot._world_state["position"].update({"x": 0.0, "y": 64.0, "z": 0.0})

    assert bot.mine_nearest("log", radius=8) is True

    commands = list(bot._executor._command_queue)
    assert commands[-1]["action"] == "mine"
    assert (commands[-1]["x"], commands[-1]["y"], commands[-1]["z"]) == (
        2, 64, 0
    )


def test_mine_block_rejects_target_without_reachable_standing_position():
    bot = Bot({})
    assert bot.mine_block((2, 64, 2)) is False
    assert not bot._executor._command_queue


def test_survival_mining_selects_suitable_hotbar_tool_and_duration():
    bot = Bot({
        "host": "localhost", "port": 25565, "username": "TestBot",
        "version": "26.1.2", "game_mode": "survival",
    })
    bot._world_state["map"][(0, 0)] = FlatChunk()
    bot._world_state["position"].update({"x": 2.0, "y": 64.0, "z": 0.0})
    bot._world_state["inventory"]["slots"][38] = {
        "id": 939, "name": "diamond_pickaxe", "count": 1
    }

    assert bot.mine_block((2, 64, 2)) is True
    commands = list(bot._executor._command_queue)
    assert {"action": "select_hotbar", "slot": 2} in commands
    assert commands[-1]["action"] == "mine"
    assert commands[-1]["duration"] == 0.3


def test_survival_mining_swaps_best_tool_from_main_inventory():
    bot = Bot({
        "host": "localhost", "port": 25565, "username": "TestBot",
        "version": "26.1.2", "game_mode": "survival",
    })
    bot._world_state["map"][(0, 0)] = FlatChunk()
    bot._world_state["position"].update({"x": 2.0, "y": 64.0, "z": 0.0})
    bot._world_state["inventory"]["selected_hotbar_slot"] = 4
    bot._world_state["inventory"]["slots"][10] = {
        "id": 939, "name": "diamond_pickaxe", "count": 1
    }

    assert bot.mine_block((2, 64, 2)) is True
    commands = list(bot._executor._command_queue)
    assert {"action": "swap_hotbar", "source_slot": 10, "hotbar_slot": 4} in commands
    assert not any(command["action"] == "select_hotbar" for command in commands)

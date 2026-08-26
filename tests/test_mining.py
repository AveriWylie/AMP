"""Tests for turning a requested block target into movement and digging commands."""

from bot import Bot


class FlatChunk:
    def get_block(self, x, y, z):
        if (x, y, z) == (2, 64, 2):
            return "stone"
        return "stone" if y <= 63 else "air"


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
        "action": "mine", "x": 2, "y": 64, "z": 2, "face": 2
    }
    assert commands[-3] == {"action": "move", "x": 2, "y": 64, "z": 1}


def test_mine_block_rejects_target_without_reachable_standing_position():
    bot = Bot({})
    assert bot.mine_block((2, 64, 2)) is False
    assert not bot._executor._command_queue

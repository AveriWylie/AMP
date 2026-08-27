from amp.bot import Bot


def _bot_with_entity(distance=2.0):
    bot = Bot({
        "host": "localhost", "port": 25565, "username": "CombatTest",
        "version": "26.1.2", "game_mode": "survival",
    })
    bot._world_state["position"].update({"x": 0.0, "y": 64.0, "z": 0.0})
    bot._world_state["entities"][42] = {
        "uuid": "test", "type": 20, "name": "cow",
        "x": 0.0, "y": 64.0, "z": distance,
    }
    return bot


def test_attack_entity_faces_swings_and_attacks_tracked_target():
    bot = _bot_with_entity()
    assert bot.attack_entity(42) is True
    commands = list(bot._executor._command_queue)
    assert [command["action"] for command in commands] == ["look", "swing", "attack"]
    assert commands[-1]["entity_id"] == 42


def test_attack_entity_rejects_missing_or_out_of_reach_target():
    bot = _bot_with_entity(distance=5.0)
    assert bot.attack_entity(42) is False
    assert bot.attack_entity(999) is False
    assert not bot._executor._command_queue

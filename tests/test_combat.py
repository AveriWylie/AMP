from amp.bot import Bot
from amp.gameplay import GameplayController


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


def test_kill_entity_approaches_and_repeats_until_target_is_removed(monkeypatch):
    world = {
        "position": {"x": 0.5, "y": 64.0, "z": 0.5},
        "entities": {42: {
            "uuid": "test", "type": 100, "name": "pig",
            "x": 0.5, "y": 64.0, "z": 5.5,
        }},
    }

    class Pathfinder:
        def find_path_near(self, start, goal, weight=1.0):
            return [
                (0, 64, 0), (0, 64, 1),
                (0, 64, 2), (0, 64, 3),
            ]

    class Executor:
        def __init__(self):
            self.commands = []
            self.attacks = 0

        def enque_command(self, command):
            self.commands.append(command)
            if command["action"] == "move":
                world["position"].update({
                    "x": command["x"], "y": command["y"], "z": command["z"],
                })
            elif command["action"] == "attack":
                self.attacks += 1
                if self.attacks == 3:
                    world["entities"].pop(42)

        def wait_until_idle(self):
            return []

    executor = Executor()
    controller = GameplayController(
        world, Pathfinder(), executor, "26.2", "survival"
    )
    delays = []
    monkeypatch.setattr("amp.gameplay.time.sleep", delays.append)

    assert controller.kill_entity(42) is True
    assert executor.attacks == 3
    assert any(command["action"] == "move" for command in executor.commands)
    assert delays == [0.3, 0.3, 0.3]


def test_kill_cooldown_accounts_for_held_weapon_speed():
    bot = _bot_with_entity()
    inventory = bot._world_state["inventory"]

    inventory["slots"][36] = {"name": "wooden_axe", "id": 1, "count": 1}
    assert bot._gameplay._attack_cooldown() == 1.3

    inventory["slots"][36] = {"name": "iron_sword", "id": 2, "count": 1}
    assert bot._gameplay._attack_cooldown() == 0.675

    inventory["slots"][36] = {"name": "mace", "id": 3, "count": 1}
    assert round(bot._gameplay._attack_cooldown(), 4) == 1.7167

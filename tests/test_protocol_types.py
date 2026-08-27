import pytest

from amp.protocol_types import (
    AttackAction,
    BlockChanged,
    ChatAction,
    HealthChanged,
    MineAction,
    MoveAction,
    PlaceAction,
    SelectHotbarAction,
    SwapHotbarAction,
    action_from_command,
    command_from_action,
)


@pytest.mark.parametrize(("command", "expected"), [
    ({"action": "move", "x": 1, "y": 64, "z": -2}, MoveAction(1, 64, -2)),
    ({"action": "chat", "message": "hello"}, ChatAction("hello")),
    ({"action": "attack", "entity_id": 9}, AttackAction(9)),
    (
        {"action": "mine", "x": 1, "y": 2, "z": 3, "face": 4, "duration": 0.5},
        MineAction(1, 2, 3, face=4, duration=0.5),
    ),
    (
        {
            "action": "place", "x": 1, "y": 2, "z": 3, "face": 5,
            "target": (2, 2, 3), "block": "stone",
        },
        PlaceAction(1, 2, 3, face=5, target=(2, 2, 3), block="stone"),
    ),
    ({"action": "select_hotbar", "slot": 4}, SelectHotbarAction(4)),
    (
        {"action": "swap_hotbar", "source_slot": 10, "hotbar_slot": 2},
        SwapHotbarAction(10, 2),
    ),
])
def test_action_conversion_preserves_existing_command_contract(command, expected):
    action = action_from_command(command)

    assert action == expected
    assert command_from_action(action) == command


def test_action_conversion_rejects_unknown_action():
    with pytest.raises(ValueError, match="Unsupported action"):
        action_from_command({"action": "teleport"})


def test_world_events_are_immutable_values():
    health = HealthChanged(18.5, 16, 2.0)
    block = BlockChanged(-1, 63, 4, 300)

    assert health.health == 18.5
    assert block.position == (-1, 63, 4)
    with pytest.raises(AttributeError):
        health.food = 20

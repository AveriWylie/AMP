"""Block registry tests for the supported Minecraft data schemas."""

from chunk import Chunk


def test_checked_in_1202_registry_uses_ranges_without_missing_state_ids():
    state_map = Chunk._build_state_map([
        {"name": "air", "minStateId": 0, "maxStateId": 0, "states": []},
        {"name": "log", "minStateId": 10, "maxStateId": 12,
         "states": [{"name": "axis", "type": "enum"}]},
    ])
    assert state_map == {0: "air", 10: "log", 11: "log", 12: "log"}


def test_pre_1202_registry_uses_explicit_state_ids():
    state_map = Chunk._build_state_map([
        {"name": "air", "states": [{"id": 0}]},
        {"name": "stone", "states": [{"id": 1}, {"id": 2}]},
    ])
    assert state_map == {0: "air", 1: "stone", 2: "stone"}

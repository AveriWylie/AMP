"""Offline pathfinder correctness tests over a small in-memory world."""

from amp.pathfinder import Pathfinder


class FakeChunk:
    """Minimal Chunk stand-in with a flat floor at y=63."""

    def __init__(self, overrides=None):
        self._overrides = overrides or {}

    def get_block(self, x, y, z):
        if (x, y, z) in self._overrides:
            return self._overrides[(x, y, z)]
        return "stone" if y <= 63 else "air"


def test_walkable_rule():
    pathfinder = Pathfinder({"map": {(0, 0): FakeChunk()}})
    assert pathfinder._is_walkable(0, 64, 0) is True
    assert pathfinder._is_walkable(0, 65, 0) is False

    blocked_head = Pathfinder({"map": {(0, 0): FakeChunk({(0, 65, 0): "stone"})}})
    assert blocked_head._is_walkable(0, 64, 0) is False

    blocked_feet = Pathfinder({"map": {(0, 0): FakeChunk({(0, 64, 0): "stone"})}})
    assert blocked_feet._is_walkable(0, 64, 0) is False


def test_leaf_canopy_is_not_safe_pathfinding_footing():
    canopy = Pathfinder({
        "map": {(0, 0): FakeChunk({(0, 63, 0): "oak_leaves"})}
    })

    assert canopy._is_walkable(0, 64, 0) is False


def test_find_path_basic():
    pathfinder = Pathfinder({"map": {(0, 0): FakeChunk()}})
    path = pathfinder.find_path((0, 64, 0), (5, 64, 0))

    assert path
    assert path[0] == (0, 64, 0)
    assert path[-1] == (5, 64, 0)
    for start, end in zip(path, path[1:]):
        assert sum(abs(a - b) for a, b in zip(start, end)) == 1


def test_mode_expansion_difference():
    wall = {}
    for z in range(8):
        wall[(5, 64, z)] = "stone"
        wall[(5, 65, z)] = "stone"

    pathfinder = Pathfinder({"map": {(0, 0): FakeChunk(wall)}})
    start, goal = (0, 64, 0), (10, 64, 0)

    guided = pathfinder.find_path(start, goal, weight=1.0)
    guided_expansions = pathfinder._last_nodes_expanded
    autonomous = pathfinder.find_path(start, goal, weight=1.5)
    autonomous_expansions = pathfinder._last_nodes_expanded

    assert guided
    assert autonomous
    assert guided_expansions > 0 and autonomous_expansions > 0
    assert autonomous_expansions <= guided_expansions

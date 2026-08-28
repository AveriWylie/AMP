"""Convert block paths into collision-safe, tick-paced player movement."""

import math


class MovementController:
    TICK_SECONDS = 0.05
    PLAYER_RADIUS = 0.3
    WALK_TICKS_PER_BLOCK = 5

    def __init__(self):
        self._vertical_velocity = 0.0
        self._position_revision = None

    def commands_for_path(self, path):
        commands = []
        for previous, target in zip(path, path[1:]):
            commands.extend(self._commands_for_edge(previous, target))
        if commands:
            commands[-1].pop("report", None)
        return commands

    def _commands_for_edge(self, previous, target):
        px, py, pz = previous
        tx, ty, tz = target
        if ty == py:
            samples = tuple(
                (step / self.WALK_TICKS_PER_BLOCK, 0.0, True)
                for step in range(1, self.WALK_TICKS_PER_BLOCK + 1)
            )
        elif ty == py + 1:
            samples = (
                (0.00, 0.4200, False),
                (0.10, 0.7532, False),
                (0.30, 1.0013, False),
                (0.55, 1.1661, False),
                (0.80, 1.2492, False),
                (1.00, 1.2522, False),
                (1.00, 1.1768, False),
                (1.00, 1.0244, False),
                (1.00, 1.0000, True),
            )
        elif ty == py - 1:
            samples = (
                (0.20, 0.0000, True),
                (0.40, 0.0000, True),
                (0.60, 0.0000, True),
                (0.80, 0.0000, False),
                (1.00, -0.0784, False),
                (1.00, -0.2336, False),
                (1.00, -0.4642, False),
                (1.00, -0.7685, False),
                (1.00, -1.0000, True),
            )
        else:
            raise ValueError(
                f"Path edge changes elevation by more than one block: "
                f"{previous} -> {target}"
            )

        start_x, start_z = px + 0.5, pz + 0.5
        delta_x, delta_z = tx - px, tz - pz
        return [
            {
                "action": "move",
                "x": start_x + delta_x * progress,
                "y": py + y_offset,
                "z": start_z + delta_z * progress,
                "on_ground": on_ground,
                "delay": self.TICK_SECONDS,
                "report": False,
            }
            for progress, y_offset, on_ground in samples
        ]

    def gravity_command(self, world_state, pathfinder):
        position = world_state["position"]
        revision = world_state.get("position_revision", 0)
        if revision != self._position_revision:
            self._vertical_velocity = 0.0
            self._position_revision = revision

        x, y, z = position["x"], position["y"], position["z"]
        block_x, block_z = math.floor(x), math.floor(z)
        if (block_x >> 4, block_z >> 4) not in world_state["map"]:
            self._vertical_velocity = 0.0
            return None
        floor_y = math.floor(y - 0.001)
        if not pathfinder._is_passable(
            pathfinder._get_block(block_x, floor_y, block_z)
        ):
            self._vertical_velocity = 0.0
            return None

        self._vertical_velocity = (
            self._vertical_velocity - 0.08
        ) * 0.98
        next_y = y + self._vertical_velocity
        landing_y = self._landing_y(
            block_x, block_z, y, next_y, pathfinder
        )
        on_ground = landing_y is not None
        if on_ground:
            next_y = landing_y
            self._vertical_velocity = 0.0

        return {
            "action": "move",
            "x": x,
            "y": next_y,
            "z": z,
            "on_ground": on_ground,
            "delay": self.TICK_SECONDS,
            "report": False,
        }

    @staticmethod
    def _landing_y(block_x, block_z, current_y, next_y, pathfinder):
        highest = math.floor(current_y - 0.001)
        lowest = math.floor(next_y - 0.001)
        for block_y in range(highest, lowest - 1, -1):
            top = block_y + 1
            block = pathfinder._get_block(block_x, block_y, block_z)
            if (
                next_y <= top <= current_y
                and not pathfinder._is_passable(block)
            ):
                return float(top)
        return None

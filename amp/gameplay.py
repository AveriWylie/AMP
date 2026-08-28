"""Resolve high-level gameplay actions into executable command sequences."""

import math
import time

from amp.mining_data import mining_plan
from amp.movement import MovementController


class GameplayController:
    TARGET_HURT_COOLDOWN = 0.55

    ATTACK_SPEEDS = {
        "sword": 1.6,
        "pickaxe": 1.2,
        "shovel": 1.0,
        "axe": 0.8,
        "hoe": 1.0,
        "trident": 1.1,
        "mace": 0.6,
    }

    def __init__(self, world_state, pathfinder, executor, version, game_mode):
        self.world_state = world_state
        self.pathfinder = pathfinder
        self.executor = executor
        self.version = version
        self.game_mode = game_mode
        self.input_mode = None
        self.movement = MovementController()

    def set_mode(self, mode):
        self.input_mode = mode

    def _enqueue_path(self, path):
        for command in self.movement.commands_for_path(path):
            self.executor.enque_command(command)

    def tick(self):
        command = self.movement.gravity_command(
            self.world_state, self.pathfinder
        )
        if command is None:
            return False
        self.executor.enque_command(command)
        return True

    def move_to(self, goal):
        pos = self.world_state["position"]
        start = (pos["x"], pos["y"], pos["z"])
        if not self.input_mode is None:
            weight = 1.5 if self.input_mode == "autonomous" else 1.0
        else:
            print("Executed pathfinding without an explicit weight for "
                  "the manhattan distance heuristic")
            weight = 1.0
        path = self.pathfinder.find_path_near(start, goal, weight=weight)

        if not path:
            print(f"No path found to {goal}")
            return False

        self._enqueue_path(path)

        return True

    def mine_block(self, target):
        """Walk within reach of a block, face it, and enqueue a mining interaction."""
        tx, ty, tz = map(int, target)
        block_name = self.pathfinder._get_block(tx, ty, tz)
        plan = None
        if self.game_mode != "creative":
            plan = mining_plan(self.version, block_name, self.world_state["inventory"])
            if plan is None:
                print(f"Cannot safely mine {block_name} at {(tx, ty, tz)} with current hotbar")
                return False
        pos = self.world_state["position"]
        start = (pos["x"], pos["y"], pos["z"])
        weight = 1.5 if self.input_mode == "autonomous" else 1.0
        faces = {
            (-1, 0): 4,
            (1, 0): 5,
            (0, -1): 2,
            (0, 1): 3,
        }
        choices = []
        for dy in (0, 1, -1):
            for (dx, dz), face in faces.items():
                standing = (tx + dx, ty + dy, tz + dz)
                if (
                    not self.pathfinder._is_walkable(*standing)
                    or not self.pathfinder._has_stable_floor(*standing)
                ):
                    continue
                eye_distance = math.dist(
                    (standing[0] + 0.5, standing[1] + 1.62, standing[2] + 0.5),
                    (tx + 0.5, ty + 0.5, tz + 0.5),
                )
                if eye_distance > 4.5:
                    continue
                path = self.pathfinder.find_path(start, standing, weight=weight)
                if path:
                    choices.append((len(path), path, face, standing))

        if not choices:
            print(f"No reachable mining position for {(tx, ty, tz)}")
            return False

        _, path, face, standing = min(choices, key=lambda choice: choice[0])
        self._enqueue_path(path)

        if plan and plan["inventory_slot"] is not None:
            if plan["inventory_slot"] < 36:
                self.executor.enque_command({
                    "action": "swap_hotbar", "source_slot": plan["inventory_slot"],
                    "hotbar_slot": plan["hotbar_slot"],
                })
            if plan["hotbar_slot"] != self.world_state["inventory"]["selected_hotbar_slot"]:
                self.executor.enque_command({
                    "action": "select_hotbar", "slot": plan["hotbar_slot"]
                })

        dx = tx + 0.5 - (standing[0] + 0.5)
        dy = ty + 0.5 - (standing[1] + 1.62)
        dz = tz + 0.5 - (standing[2] + 0.5)
        horizontal = math.hypot(dx, dz)
        self.executor.enque_command({
            "action": "look",
            "yaw": math.degrees(math.atan2(-dx, dz)),
            "pitch": math.degrees(-math.atan2(dy, horizontal)),
        })
        self.executor.enque_command({
            "action": "mine", "x": tx, "y": ty, "z": tz, "face": face,
            "duration": plan["seconds"] if plan else 0,
        })
        return True

    def mine_nearest(self, requested_block, radius=8):
        """Find and mine the nearest reachable matching block in loaded data."""
        radius = max(1, min(int(radius), 16))
        position = self.world_state["position"]
        origin = (
            math.floor(position["x"]),
            math.floor(position["y"]),
            math.floor(position["z"]),
        )

        def matches(block_name):
            if requested_block == "log":
                return block_name.endswith("_log")
            return block_name == requested_block

        candidates = []
        ox, oy, oz = origin
        for x in range(ox - radius, ox + radius + 1):
            for y in range(oy - radius, oy + radius + 1):
                for z in range(oz - radius, oz + radius + 1):
                    if matches(self.pathfinder._get_block(x, y, z)):
                        distance = (x - ox) ** 2 + (y - oy) ** 2 + (z - oz) ** 2
                        candidates.append((distance, (x, y, z)))

        if not candidates:
            print(f"Block '{requested_block}' not found in loaded blocks")
            return False

        for _, target in sorted(candidates)[:32]:
            if self.mine_block(target):
                return True

        print(f"No reachable {requested_block} found in loaded blocks")
        return False

    def place_block(self, target, block_name):
        """Walk within reach, equip a block stack, and place against a solid support."""
        tx, ty, tz = map(int, target)
        if not self.pathfinder._is_passable(
            self.pathfinder._get_block(tx, ty, tz)
        ):
            print(f"Placement target {(tx, ty, tz)} is occupied")
            return False

        inventory = self.world_state["inventory"]
        matching = [
            (slot, item) for slot, item in inventory["slots"].items()
            if 9 <= slot <= 44 and item["name"] == block_name and item["count"] > 0
        ]
        if not matching:
            print(f"No {block_name} in player inventory")
            return False
        source_slot, _ = max(matching, key=lambda entry: entry[1]["count"])
        hotbar_slot = (
            source_slot - 36 if source_slot >= 36 else inventory["selected_hotbar_slot"]
        )

        # Each tuple is support offset plus the face of that support clicked toward target.
        supports = (
            ((0, -1, 0), 1), ((0, 1, 0), 0),
            ((0, 0, -1), 3), ((0, 0, 1), 2),
            ((-1, 0, 0), 5), ((1, 0, 0), 4),
        )
        solid_supports = []
        for (sx, sy, sz), face in supports:
            support = (tx + sx, ty + sy, tz + sz)
            if not self.pathfinder._is_passable(
                self.pathfinder._get_block(*support)
            ):
                solid_supports.append((support, face))
        if not solid_supports:
            print(f"No solid support beside placement target {(tx, ty, tz)}")
            return False

        pos = self.world_state["position"]
        start = (pos["x"], pos["y"], pos["z"])
        weight = 1.5 if self.input_mode == "autonomous" else 1.0
        choices = []
        for support, face in solid_supports:
            for dy in (0, 1, -1):
                for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    standing = (tx + dx, ty + dy, tz + dz)
                    if standing == (tx, ty, tz) or not self.pathfinder._is_walkable(*standing):
                        continue
                    distance = math.dist(
                        (standing[0] + 0.5, standing[1] + 1.62, standing[2] + 0.5),
                        (support[0] + 0.5, support[1] + 0.5, support[2] + 0.5),
                    )
                    if distance > 4.5:
                        continue
                    path = self.pathfinder.find_path(start, standing, weight=weight)
                    if path:
                        choices.append((len(path), path, standing, support, face))
        if not choices:
            print(f"No reachable placement position for {(tx, ty, tz)}")
            return False

        _, path, standing, support, face = min(choices, key=lambda choice: choice[0])
        self._enqueue_path(path)
        if source_slot < 36:
            self.executor.enque_command({
                "action": "swap_hotbar", "source_slot": source_slot,
                "hotbar_slot": hotbar_slot,
            })
        if hotbar_slot != inventory["selected_hotbar_slot"]:
            self.executor.enque_command({"action": "select_hotbar", "slot": hotbar_slot})

        dx = support[0] + 0.5 - (standing[0] + 0.5)
        dy = support[1] + 0.5 - (standing[1] + 1.62)
        dz = support[2] + 0.5 - (standing[2] + 0.5)
        self.executor.enque_command({
            "action": "look", "yaw": math.degrees(math.atan2(-dx, dz)),
            "pitch": math.degrees(-math.atan2(dy, math.hypot(dx, dz))),
        })
        self.executor.enque_command({
            "action": "place", "x": support[0], "y": support[1], "z": support[2],
            "face": face, "target": (tx, ty, tz), "block": block_name,
        })
        return True

    def attack_entity(self, entity_id):
        """Face and attack a tracked entity when it is within normal survival reach."""
        entity = self.world_state["entities"].get(int(entity_id))
        if entity is None:
            print(f"Entity {entity_id} is not currently tracked")
            return False
        position = self.world_state["position"]
        eye = (position["x"], position["y"] + 1.62, position["z"])
        target = (entity["x"], entity["y"] + 0.9, entity["z"])
        if math.dist(eye, target) > 3.0:
            print(f"Entity {entity_id} is outside attack reach")
            return False
        dx, dy, dz = (target[index] - eye[index] for index in range(3))
        self.executor.enque_command({
            "action": "look", "yaw": math.degrees(math.atan2(-dx, dz)),
            "pitch": math.degrees(-math.atan2(dy, math.hypot(dx, dz))),
        })
        self.executor.enque_command({"action": "swing", "hand": 0})
        self.executor.enque_command({"action": "attack", "entity_id": int(entity_id)})
        return True

    def kill_entity(self, entity_id, max_attacks=20):
        """Approach and attack a tracked entity until the server removes it."""
        entity_id = int(entity_id)
        attacked = False
        attacks = 0
        attempts = 0
        while attacks < max_attacks and attempts < max_attacks * 3:
            attempts += 1
            entity = self.world_state["entities"].get(entity_id)
            if entity is None:
                return attacked

            position = self.world_state["position"]
            eye = (position["x"], position["y"] + 1.62, position["z"])
            target = (entity["x"], entity["y"] + 0.9, entity["z"])
            if math.dist(eye, target) > 3.0:
                start = (position["x"], position["y"], position["z"])
                weight = 1.5 if self.input_mode == "autonomous" else 1.0
                path = self.pathfinder.find_path_near(
                    start, (entity["x"], entity["y"], entity["z"]),
                    weight=weight, radius=1,
                )
                if not path:
                    print(f"No path into attack reach of entity {entity_id}")
                    return False
                self._enqueue_path(path)
                self.executor.wait_until_idle()

            if not self.attack_entity(entity_id):
                continue
            attacked = True
            attacks += 1
            self.executor.wait_until_idle()
            time.sleep(self._attack_cooldown())

        print(f"Entity {entity_id} survived {attacks} attacks")
        return False

    def _attack_cooldown(self):
        inventory = self.world_state.get("inventory", {})
        selected = inventory.get("selected_hotbar_slot", 0)
        item = inventory.get("slots", {}).get(36 + selected)
        name = item.get("name", "") if item else ""
        speed = 4.0
        for suffix, candidate in self.ATTACK_SPEEDS.items():
            if name == suffix or name.endswith(f"_{suffix}"):
                speed = candidate
                break
        weapon_cooldown = 1.0 / speed + 0.05
        return max(weapon_cooldown, self.TARGET_HURT_COOLDOWN)

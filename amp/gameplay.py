"""
--------------------------------------------------------------------------------------------
Gameplay Module - Intentions into action sequences
--------------------------------------------------------------------------------------------
Turns one intention into the queue of actions that carries it out. "Mine that block" is not a
packet, it is walk somewhere you can reach it from, equip the right tool, turn to face it,
then break it, and this is where that expansion happens.

Everything here enqueues rather than executing. It decides what should happen and in what
order, execution decides when, which is what keeps the tick loop free while a whole multi-step
sequence is being planned.

Two constants recur throughout. 1.62 is eye height, so reach is measured from where the camera
is rather than the feet, and 0.5 centres a position in its block, since block coordinates name
a corner while the player stands in the middle.

Two reach limits recur as well, 4.5 for blocks and 3.0 for entities, because vanilla gives
block interaction a longer arm than attacking.

Every public method answers True or False rather than raising. A goal that cannot be reached
is an ordinary outcome the planner has to reason about, not an error, and it needs to be told
which of its steps failed rather than having the batch collapse.
--------------------------------------------------------------------------------------------
"""

# Imports
import math
import time
from amp.mining_data import mining_plan
from amp.movement import MovementController


"""
--------------------------------------------------------------------------------------------
Class Header - Gameplay controller
--------------------------------------------------------------------------------------------
Holds the four collaborators it coordinates and none of the state they own. world_state is
read live rather than snapshotted, so a plan built now sees chunks that arrived a moment ago.

input_mode decides the pathfinding weight, 1.5 in autonomous for faster and slightly worse
paths, 1.0 in guided where a human is waiting on a good one. It starts as None, which move_to
warns about rather than silently defaulting.

game_mode matters only to mining, since creative needs no tool and skips the plan entirely.
--------------------------------------------------------------------------------------------
"""
class GameplayController:
    # Vanilla's post-hit invulnerability, ten ticks. Attacking inside it does nothing at all,
    # so it is the floor on how fast repeated attacks are worth sending.
    TARGET_HURT_COOLDOWN = 0.55

    # Attacks per second by tool type. Swords are fastest, axes hit hardest but slowest, and
    # anything not listed falls back to bare hands at 4.0.
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


    # Paths now go through MovementController rather than becoming one move per node, so a drop
    # off a ledge becomes a falling arc instead of a teleport the server would reject.
    def _enqueue_path(self, path):
        for command in self.movement.commands_for_path(path):
            self.executor.enque_command(command)


    # Called by the lifecycle loop on any tick where nothing was queued. Returns whether it
    # enqueued anything, so the caller knows to execute it in this tick rather than the next.
    # Only runs when idle, which is what keeps gravity from fighting a deliberate walk.
    def tick(self):
        command = self.movement.gravity_command(self.world_state, self.pathfinder)

        if command is None:
            return False

        self.executor.enque_command(command)
        return True


    """
    --------------------------------------------------------------------------------------------
    Function Header - Move to
    --------------------------------------------------------------------------------------------
    Walks toward a goal, using find_path_near rather than find_path because a goal handed down
    from a planner is approximate, and often names something solid like a tree trunk that can
    never be stood in.

    Warns and falls back to 1.0 when no mode is set, rather than defaulting silently, because
    the weight changes path quality and a silent default hides which one ran.
    --------------------------------------------------------------------------------------------
    """
    def move_to(self, goal):
        pos = self.world_state["position"]
        start = (pos["x"], pos["y"], pos["z"])

        if not self.input_mode is None:
            weight = 1.5 if self.input_mode == "autonomous" else 1.0
        else:
            print("Executed pathfinding without an explicit weight for the manhattan distance heuristic")
            weight = 1.0

        path = self.pathfinder.find_path_near(start, goal, weight=weight)

        if not path:
            print(f"No path found to {goal}")
            return False

        self._enqueue_path(path)

        return True


    """
    --------------------------------------------------------------------------------------------
    Function Header - Mine block
    --------------------------------------------------------------------------------------------
    Walks within reach of one block, faces it, and enqueues the mining interaction.

    Survival checks the tool first and gives up before walking anywhere, since arriving at a
    block you cannot harvest wastes the whole trip. Creative skips the plan, nothing there
    needs a tool.

    Candidate standing positions are the four cardinal neighbours at three heights, filtered to
    ones that are walkable, have a stable floor, and sit within 4.5 of the block measured from
    eye height. The stable floor matters specifically here, mining from a leaf perch tends to
    end with a fall once the tree comes down.

    The shortest path wins rather than the closest position, because a neighbour one block away
    through a wall is much further in practice than one three blocks away across open ground.

    Tool equipping is two steps and both are conditional. A tool outside the hotbar has to be
    swapped in first, slots below 36 being the main inventory, and selecting is skipped when
    the right slot is already held.

    The look angles use atan2(-dx, dz) because Minecraft measures yaw clockwise from south
    rather than the usual counter-clockwise from east, and pitch is negated because positive
    pitch points down.
    --------------------------------------------------------------------------------------------
    """
    def mine_block(self, target):
        tx, ty, tz = map(int, target)
        block_name = self.pathfinder._get_block(tx, ty, tz)
        plan = None

        # checked before walking anywhere, an unharvestable block is a wasted trip
        if self.game_mode != "creative":
            plan = mining_plan(self.version, block_name, self.world_state["inventory"])
            if plan is None:
                print(f"Cannot safely mine {block_name} at {(tx, ty, tz)} with current hotbar")
                return False

        pos = self.world_state["position"]
        start = (pos["x"], pos["y"], pos["z"])
        weight = 1.5 if self.input_mode == "autonomous" else 1.0
        # neighbour offset to the block face clicked toward the target
        faces = {(-1, 0): 4,(1, 0): 5,(0, -1): 2,(0, 1): 3}
        choices = []

        for dy in (0, 1, -1):
            for (dx, dz), face in faces.items():
                standing = (tx + dx, ty + dy, tz + dz)
                # stable floor matters here, mining from a leaf perch ends in a fall
                if (not self.pathfinder._is_walkable(*standing) or not self.pathfinder._has_stable_floor(*standing)):
                    continue

                eye_distance = math.dist(
                    (standing[0] + 0.5, standing[1] + 1.62, standing[2] + 0.5), (tx + 0.5, ty + 0.5, tz + 0.5)
                )

                # vanilla block interaction range, measured from eye height not the feet
                if eye_distance > 4.5:
                    continue

                path = self.pathfinder.find_path(start, standing, weight=weight)

                if path:
                    choices.append((len(path), path, face, standing))

        if not choices:
            print(f"No reachable mining position for {(tx, ty, tz)}")
            return False

        # shortest path, not nearest position, a neighbour behind a wall is further in practice
        _, path, face, standing = min(choices, key=lambda choice: choice[0])
        self._enqueue_path(path)

        if plan and plan["inventory_slot"] is not None:
            # below 36 is main inventory, so the tool has to be swapped into the hotbar first
            if plan["inventory_slot"] < 36:
                self.executor.enque_command({
                    "action": "swap_hotbar", "source_slot": plan["inventory_slot"],
                    "hotbar_slot": plan["hotbar_slot"],
                })

            if plan["hotbar_slot"] != self.world_state["inventory"]["selected_hotbar_slot"]:
                self.executor.enque_command({"action": "select_hotbar", "slot": plan["hotbar_slot"]})

        dx = tx + 0.5 - (standing[0] + 0.5)
        dy = ty + 0.5 - (standing[1] + 1.62)
        dz = tz + 0.5 - (standing[2] + 0.5)
        # yaw is clockwise from south, hence atan2(-dx, dz), and positive pitch points down
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


    """
    --------------------------------------------------------------------------------------------
    Function Header - Mine nearest
    --------------------------------------------------------------------------------------------
    Finds the closest matching block already in loaded chunk data and mines it. Only loaded
    data, so this never waits on the server, it works with whatever has arrived.

    Radius is clamped to 16 because the scan is a cube and its cost grows with the cube of the
    radius. 16 is already 35937 lookups, and 32 would be eight times that over terrain which is
    mostly not loaded anyway.

    "log" is special-cased to a suffix match, since a planner asking for a log means any wood
    type and no block is actually called log.

    Sorting by squared distance orders identically to real distance without the square root.
    Only the closest 32 are attempted, because each attempt runs a full pathfinding search and
    blocks that no route reaches are usually walled off together with their neighbours.
    --------------------------------------------------------------------------------------------
    """
    def mine_nearest(self, requested_block, radius=8):
        # cubic scan, so the ceiling matters, 16 is already ~36k lookups
        radius = max(1, min(int(radius), 16))
        position = self.world_state["position"]
        origin = (math.floor(position["x"]), math.floor(position["y"]), math.floor(position["z"]),)

        # "log" means any wood type, no block is called log on its own
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
                        # squared distance sorts the same as real distance, without the sqrt
                        distance = (x - ox) ** 2 + (y - oy) ** 2 + (z - oz) ** 2
                        candidates.append((distance, (x, y, z)))

        if not candidates:
            print(f"Block '{requested_block}' not found in loaded blocks")
            return False

        # each attempt runs a full search, and unreachable blocks cluster, so cap the tries
        for _, target in sorted(candidates)[:32]:
            if self.mine_block(target):
                return True

        print(f"No reachable {requested_block} found in loaded blocks")
        return False


    """
    --------------------------------------------------------------------------------------------
    Function Header - Place block
    --------------------------------------------------------------------------------------------
    Walks within reach, equips a block stack, and places against a solid support.

    Placement is not "put a block at these coordinates". The protocol places by clicking a face
    of an existing block, and the new one appears in the empty space beside it. So the target
    has to be empty and something next to it has to be solid, and both are checked before any
    walking happens.

    Six supports are tried, one per face, each paired with the face of that support pointing
    back at the target. The stack with the largest count is chosen so partial stacks get used
    up rather than fragmenting further.

    Standing positions exclude the target itself, since standing in the space would block the
    placement.

    Reach is measured to the support rather than the target, because the support is the block
    actually being clicked.
    --------------------------------------------------------------------------------------------
    """
    def place_block(self, target, block_name):
        tx, ty, tz = map(int, target)

        # the space has to be empty, you cannot place into an occupied block
        if not self.pathfinder._is_passable(self.pathfinder._get_block(tx, ty, tz)):
            print(f"Placement target {(tx, ty, tz)} is occupied")
            return False

        inventory = self.world_state["inventory"]

        matching = [(slot, item) for slot, item in inventory["slots"].items()
                    if 9 <= slot <= 44 and item["name"] == block_name and item["count"] > 0]

        if not matching:
            print(f"No {block_name} in player inventory")
            return False

        # biggest stack, so partial stacks get consumed rather than fragmenting further
        source_slot, _ = max(matching, key=lambda entry: entry[1]["count"])
        hotbar_slot = (source_slot - 36 if source_slot >= 36 else inventory["selected_hotbar_slot"])

        # Each tuple is support offset plus the face of that support clicked toward target.
        supports = (
            ((0, -1, 0), 1), ((0, 1, 0), 0),
            ((0, 0, -1), 3), ((0, 0, 1), 2),
            ((-1, 0, 0), 5), ((1, 0, 0), 4),
        )

        solid_supports = []

        for (sx, sy, sz), face in supports:
            support = (tx + sx, ty + sy, tz + sz)
            if not self.pathfinder._is_passable(self.pathfinder._get_block(*support)):
                solid_supports.append((support, face))

        # nothing solid to click means the block has nothing to attach to
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
                    # standing in the target space would block the placement itself
                    if standing == (tx, ty, tz) or not self.pathfinder._is_walkable(*standing):
                        continue

                    distance = math.dist(
                        (standing[0] + 0.5, standing[1] + 1.62, standing[2] + 0.5),
                        (support[0] + 0.5, support[1] + 0.5, support[2] + 0.5)
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
            self.executor.enque_command({"action": "swap_hotbar","source_slot": source_slot,"hotbar_slot": hotbar_slot})

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

    """
    --------------------------------------------------------------------------------------------
    Function Header - Attack entity
    --------------------------------------------------------------------------------------------
    Faces and attacks a tracked entity when it is already within survival reach. One swing, not
    a pursuit, kill_entity is the version that closes distance.

    Only entities world state already knows about, since the attack packet needs the numeric ID
    the server assigned and that cannot be inferred from a position.

    Reach is 3.0 rather than the 4.5 used for blocks, entity reach being shorter in survival,
    and it is measured eye to roughly mid-body, hence the 0.9, because aiming at an entity's
    feet misses at close range.

    Swing before attack, since the swing is the visible animation and the attack is the damage,
    and vanilla sends both.
    --------------------------------------------------------------------------------------------
    """
    def attack_entity(self, entity_id):
        entity = self.world_state["entities"].get(int(entity_id))

        if entity is None:
            print(f"Entity {entity_id} is not currently tracked")
            return False

        position = self.world_state["position"]
        eye = (position["x"], position["y"] + 1.62, position["z"])
        target = (entity["x"], entity["y"] + 0.9, entity["z"])

        # entity reach is shorter than block reach, and this refuses rather than chasing
        if math.dist(eye, target) > 3.0:
            print(f"Entity {entity_id} is outside attack reach")
            return False

        dx, dy, dz = (target[index] - eye[index] for index in range(3))

        self.executor.enque_command({
            "action": "look", "yaw": math.degrees(math.atan2(-dx, dz)),
            "pitch": math.degrees(-math.atan2(dy, math.hypot(dx, dz))),
        })

        # swing is the animation, attack is the damage, vanilla sends both
        self.executor.enque_command({"action": "swing", "hand": 0})
        self.executor.enque_command({"action": "attack", "entity_id": int(entity_id)})

        return True

    """
    --------------------------------------------------------------------------------------------
    Function Header - Kill entity
    --------------------------------------------------------------------------------------------
    Approach and attack a tracked entity until the server removes it. attack_entity is one
    swing at something already in reach, this is the goal, so it closes distance, keeps hitting,
    and only stops when the entity is gone or the clock runs out.

    Completion is the entity disappearing from world state, not a health value, because a mob's
    health is never sent to other clients. The server removing it is the only observable death.

    Returns whether anything was actually hit rather than whether the mob died, since another
    player or a fall can finish it, and that still counts as the goal being met.

    The approach uses radius=1 so it stops as close as it can. A wider search would happily pick
    a standing spot outside the 3.0 attack reach and the loop would approach forever.

    Every attack waits for the queue to drain before the next, so the cooldown is measured from
    when the swing actually landed rather than from when it was queued.

    The failure and timeout paths differ deliberately. No path returns immediately, since
    nothing will change by trying again, while the timeout keeps going and reports how many
    attacks it managed, which is what distinguishes "could not reach it" from "hit it 40 times
    and it will not die".
    --------------------------------------------------------------------------------------------
    """
    def kill_entity(self, entity_id, timeout=60):
        entity_id = int(entity_id)
        attacked = False
        attacks = 0
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            entity = self.world_state["entities"].get(entity_id)

            # gone from world state is the only death we can observe, mob health is not sent
            if entity is None:
                return attacked

            position = self.world_state["position"]
            eye = (position["x"], position["y"] + 1.62, position["z"])
            target = (entity["x"], entity["y"] + 0.9, entity["z"])

            # out of reach, so close the distance first
            if math.dist(eye, target) > 3.0:
                start = (position["x"], position["y"], position["z"])
                weight = 1.5 if self.input_mode == "autonomous" else 1.0
                # radius=1 keeps the landing spot inside attack reach, wider would loop forever
                path = self.pathfinder.find_path_near(start, (entity["x"], entity["y"], entity["z"]), weight=weight, radius=1)

                # unreachable now means unreachable next pass too, so stop rather than spin
                if not path:
                    print(f"No path into attack reach of entity {entity_id}")
                    return False

                self._enqueue_path(path)
                self.executor.wait_until_idle()

            # the mob moved out of reach again mid-approach, so go round and re-path
            if not self.attack_entity(entity_id):
                continue

            attacked = True
            attacks += 1
            # drain first, so the cooldown is timed from the landed swing not the queued one
            self.executor.wait_until_idle()
            time.sleep(self._attack_cooldown())

        print(f"Timed out trying to kill entity {entity_id} after {attacks} attacks")
        return False

    """
    --------------------------------------------------------------------------------------------
    Function Header - Attack cooldown
    --------------------------------------------------------------------------------------------
    How long to wait between attacks, which is the larger of two independent limits.

    The weapon has a cooldown, since a swing before the attack bar refills does reduced damage.
    That comes from the held item's attack speed, plus a small margin so a swing never lands
    fractionally early. Anything unrecognised is treated as a bare hand at 4.0 per second.

    The target has one too, ten ticks of invulnerability after being hit, during which an attack
    does nothing whatsoever.

    Taking the maximum respects both. A fast sword is limited by the mob's invulnerability, a
    slow axe by its own recovery, and either way no swing is wasted.

    Matching allows both an exact name and a suffix, so "trident" and "diamond_sword" both
    resolve while a "wooden_sword" is not mistaken for something else.
    --------------------------------------------------------------------------------------------
    """
    def _attack_cooldown(self):
        inventory = self.world_state.get("inventory", {})
        selected = inventory.get("selected_hotbar_slot", 0)
        # hotbar slots are 36-44 in screen order, so offset the selected index
        item = inventory.get("slots", {}).get(36 + selected)
        name = item.get("name", "") if item else ""
        # bare hands, the fallback for an empty slot or an unrecognised item
        speed = 4.0

        for suffix, candidate in self.ATTACK_SPEEDS.items():
            if name == suffix or name.endswith(f"_{suffix}"):
                speed = candidate
                break

        # small margin so a swing never lands fractionally before the bar refills
        weapon_cooldown = 1.0 / speed + 0.05
        # whichever limit is longer, both have to be respected
        return max(weapon_cooldown, self.TARGET_HURT_COOLDOWN)

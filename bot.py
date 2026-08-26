"""
--------------------------------------------------------------------------------------------
Bot Module
--------------------------------------------------------------------------------------------
TCP connection, varint encoding/decoding, handshake/login, a keepalive loop, validation
layers for bot configuration, and a singular entry point to start all these processes
(bot.start()). With this architecture the only external interface / process of an external
interface is the code blow:

bot = Bot(config)
bot.start()
--------------------------------------------------------------------------------------------
"""
# imports
import os
import math
import threading
import time
from collections import deque
from connection import Connection
from execution import Execute
from pathfinder import PASSABLE, Pathfinder
from planner import Planner
from protocol_data import packet_ids_for_protocol, version_protocols
from mining_data import mining_plan
from world_state import WorldStateTracker
from dotenv import load_dotenv

"""
--------------------------------------------------------------------------------------------
Class Header - Bot initialization
--------------------------------------------------------------------------------------------
"""
class Bot:
    version_protocol = version_protocols()

    """
    --------------------------------------------------------------------------------------------
    Function Header - Constants field
    --------------------------------------------------------------------------------------------
    Within Bot to avoid duplication of constants for each Bot object. Explicitely we are saying 
    username/host has no restricted range of allowed possibilities (same as saying "username": 
    None ... etc.).
    --------------------------------------------------------------------------------------------
    """
    allowed_values = {"game_mode": {"survival", "creative", "superflat", "adventure", "spectator"},
                      "behavior_mode": {"passive", "aggressive", "neutral"}, "port": range(1024, 65536),
                      "version": set(version_protocol)}

    default_values = {"host": "localhost", "port": 25565, "username": "Guest", "version": "1.20.2",
        "game_mode": "survival", "behavior_mode": "passive"}

    """
    --------------------------------------------------------------------------------------------
    Function Header - Version to protocol map
    --------------------------------------------------------------------------------------------
    The handshake sends a protocol number, not a version string, and every packet ID is keyed
    to that number. The generated table covers the supported legacy Login -> Play transition
    and Minecraft 1.20.2's Login -> Configuration -> Play transition.
    --------------------------------------------------------------------------------------------
    """
    # ------------------------------------------------------------------------------------------

    """
    --------------------------------------------------------------------------------------------
    Function Header - Validation Layer
    --------------------------------------------------------------------------------------------
    For configuration input which is retrived interactively, (meaning that it is 
    quite simple and need not be more complicated then this) the range of possible 
    inputs is small and easily describable through sets, and existence notation which 
    translates to logic directly, and is thus, easily codable / understandable.
    --------------------------------------------------------------------------------------------
    """
    def _validate_input(self):
        self._valid_flags = {}

        for key in self.default_values:
            # key is a string as we retrieve it by iterating over default values an f-string
            # which lets you embed a variable inside a string. So f"_{key}" produces "_host"
            # when key is "host", which matches the actual attribute name self._host. To
            # validate we only use string data types.
            value = getattr(self, f"_{key}")
            allowed = self.allowed_values.get(key)

            # we handle param keys that have no restricted range as mentioned above
            # if theres no restricted range then is_valid is true for any none empty input
            if allowed is None:
                is_valid = value is not None
            else:
                is_valid = value in allowed

            self._valid_flags[key] = is_valid

            if not is_valid:
                setattr(self, f"_{key}", self.default_values[key])

    def __init__(self, config):
        # config.get("..") = config[""] in esence and efficiency however .get
        # checks self._host = config.get("host", None) implicitely which will be
        # needed in the validation layer we can now handle error input later on
        # self._ means outside of this class you cannot internally reference the object
        # to have complete encapsulation we provide implmentation for any needed change to
        # internal data, seperating the implmentation from interface
        self._host = config.get("host")
        self._port = config.get("port")
        self._version = config.get("version")
        self._username = config.get("username")
        self._game_mode = config.get("game_mode")
        self._behavior_mode = config.get("behavior_mode")
        # implemented with a deqeue or for efficient popping
        self._command_queue = deque()
        self._valid_flags = {}
        # guarantee the object is always in a valid state immediately after
        # creation with config get
        self._validate_input()
        # protocol number resolved from the validated version, not hardcoded, so the handshake
        # and the server agree. Unmapped versions fall back to 762 (see version_protocol above).
        protocol = self.version_protocol.get(self._version)
        if protocol is None:
            print(f"Warning: version '{self._version}' not supported by this base "
                  f"(supported: {list(self.version_protocol)}). Falling back to protocol 762 (1.19.4).")
            protocol = 762
        self.play_ids = packet_ids_for_protocol(protocol, "clientbound")
        self._connection = Connection(
            self._host, self._port, self._version, self._username,
            on_failure=self._handle_failure, protocol_version=protocol,
        )
        self._world_tracker = WorldStateTracker(
            self._version, self._connection, self.play_ids
        )
        self._world_state = self._world_tracker.state
        self._on_packet = self._world_tracker._on_packet
        self._handle_position = self._world_tracker._handle_position
        self._confirm_position = self._world_tracker._confirm_position
        self._handle_health = self._world_tracker._handle_health
        self._respawn = self._world_tracker._respawn
        self._handle_entity = self._world_tracker._handle_entity
        self._handle_entity_move = self._world_tracker._handle_entity_move
        self._handle_entity_teleport = self._world_tracker._handle_entity_teleport
        self._handle_entity_destroy = self._world_tracker._handle_entity_destroy
        self._handle_chunk = self._world_tracker._handle_chunk
        self._handle_block_update = self._world_tracker._handle_block_update
        self._skip_nbt_payload = self._world_tracker._skip_nbt_payload
        self._decode_slot = self._world_tracker._decode_slot
        self._handle_window_items = self._world_tracker._handle_window_items
        self._handle_set_slot = self._world_tracker._handle_set_slot
        self._connection._packet_handler = self._world_tracker._on_packet
        self._input_mode = None
        self._pathfinder = Pathfinder(self._world_state)
        self._executor = Execute(
            self._connection,
            game_mode=config.get("game_mode", "survival"),
            behavior_mode=config.get("behavior_mode", "neutral"),
            world_state=self._world_state,
        )
        self._execution_started = False
        self._execution_thread = None
        # Load local development credentials without overriding environment variables
        # supplied by a shell, CI runner, or deployment platform.
        load_dotenv()
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            try:
                with open("api_key.txt", "r", encoding="utf-8") as f:
                    api_key = f.read().strip()
            except FileNotFoundError:
                api_key = None
                print("Warning: ANTHROPIC_API_KEY not found, planner will not function")

        self._planner = Planner(
            self._world_state,
            api_key,
            model=os.environ.get("ANTHROPIC_MODEL"),
        )
        self._run_thread = None

    # entrance for cli
    def start(self):
        if not all(self._valid_flags.values()):
            invalid = [k for k, v in self._valid_flags.items() if not v]
            print(f"Warning: fields fell back to defaults: {invalid}")

        try:
            # rather then call bot.start as this is post validation -> less overhead
            self._connection.connect()
            self._start_execution()
            print(f"Bot '{self._username}' started on {self._host}:{self._port}")

        except ConnectionError as e:
            print(f"Failed to start: {e}")
            self._handle_failure(e)

        except Exception as e:
            print(f"Unexpected error during start: {e}")
            self._connection.disconnect()

    def disconnect(self):
        """Disconnect through the Bot lifecycle boundary."""
        self._connection.disconnect()

    def set_mode(self, mode):
        self._input_mode = mode

    """
    --------------------------------------------------------------------------------------------
    Function Header - move_to
    --------------------------------------------------------------------------------------------
    Public interface for movement. Takes a goal (x, y, z) tuple, finds a path from the bot's
    current position using A* weighted by behavior mode, and enqueues each step as a move
    command on the executor.

    Returns True if a path was found and enqueued, False if no path exists.

    The executor then sends each move as a movement packet when execute_queue is called by the 
    execution loop.

    Derives weight inline from self._input_mode as the cli asks for the mode, the user picks 
    guided or autonomous in select_mode(), then bot.set_mode(mode) sets _input_mode, and 
    move_to derives weight from that inline. The user never sees or touches the weight directly.
    --------------------------------------------------------------------------------------------
    """
    def move_to(self, goal):
        pos = self._world_state["position"]
        start = (pos["x"], pos["y"], pos["z"])
        if not self._input_mode is None:
            weight = 1.5 if self._input_mode == "autonomous" else 1.0
        else:
            print("Executed pathfinding without an explicit weight for "
                  "the manhattan distance heuristic")
            weight = 1.0
        path = self._pathfinder.find_path(start, goal, weight=weight)

        if not path:
            print(f"No path found to {goal}")
            return False

        for x, y, z in path:
            self._executor.enque_command({"action": "move", "x": x, "y": y, "z": z})

        return True

    def mine_block(self, target):
        """Walk within reach of a block, face it, and enqueue a mining interaction."""
        tx, ty, tz = map(int, target)
        block_name = self._pathfinder._get_block(tx, ty, tz)
        plan = None
        if self._game_mode != "creative":
            plan = mining_plan(self._version, block_name, self._world_state["inventory"])
            if plan is None:
                print(f"Cannot safely mine {block_name} at {(tx, ty, tz)} with current hotbar")
                return False
        pos = self._world_state["position"]
        start = (pos["x"], pos["y"], pos["z"])
        weight = 1.5 if self._input_mode == "autonomous" else 1.0
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
                if not self._pathfinder._is_walkable(*standing):
                    continue
                eye_distance = math.dist(
                    (standing[0] + 0.5, standing[1] + 1.62, standing[2] + 0.5),
                    (tx + 0.5, ty + 0.5, tz + 0.5),
                )
                if eye_distance > 4.5:
                    continue
                path = self._pathfinder.find_path(start, standing, weight=weight)
                if path:
                    choices.append((len(path), path, face, standing))

        if not choices:
            print(f"No reachable mining position for {(tx, ty, tz)}")
            return False

        _, path, face, standing = min(choices, key=lambda choice: choice[0])
        for x, y, z in path:
            self._executor.enque_command({"action": "move", "x": x, "y": y, "z": z})

        if plan and plan["inventory_slot"] is not None:
            if plan["inventory_slot"] < 36:
                self._executor.enque_command({
                    "action": "swap_hotbar", "source_slot": plan["inventory_slot"],
                    "hotbar_slot": plan["hotbar_slot"],
                })
            if plan["hotbar_slot"] != self._world_state["inventory"]["selected_hotbar_slot"]:
                self._executor.enque_command({
                    "action": "select_hotbar", "slot": plan["hotbar_slot"]
                })

        dx = tx + 0.5 - (standing[0] + 0.5)
        dy = ty + 0.5 - (standing[1] + 1.62)
        dz = tz + 0.5 - (standing[2] + 0.5)
        horizontal = math.hypot(dx, dz)
        self._executor.enque_command({
            "action": "look",
            "yaw": math.degrees(math.atan2(-dx, dz)),
            "pitch": math.degrees(-math.atan2(dy, horizontal)),
        })
        self._executor.enque_command({
            "action": "mine", "x": tx, "y": ty, "z": tz, "face": face,
            "duration": plan["seconds"] if plan else 0,
        })
        return True

    def place_block(self, target, block_name):
        """Walk within reach, equip a block stack, and place against a solid support."""
        tx, ty, tz = map(int, target)
        if self._pathfinder._get_block(tx, ty, tz) not in PASSABLE:
            print(f"Placement target {(tx, ty, tz)} is occupied")
            return False

        inventory = self._world_state["inventory"]
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
            if self._pathfinder._get_block(*support) not in PASSABLE:
                solid_supports.append((support, face))
        if not solid_supports:
            print(f"No solid support beside placement target {(tx, ty, tz)}")
            return False

        pos = self._world_state["position"]
        start = (pos["x"], pos["y"], pos["z"])
        weight = 1.5 if self._input_mode == "autonomous" else 1.0
        choices = []
        for support, face in solid_supports:
            for dy in (0, 1, -1):
                for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    standing = (tx + dx, ty + dy, tz + dz)
                    if standing == (tx, ty, tz) or not self._pathfinder._is_walkable(*standing):
                        continue
                    distance = math.dist(
                        (standing[0] + 0.5, standing[1] + 1.62, standing[2] + 0.5),
                        (support[0] + 0.5, support[1] + 0.5, support[2] + 0.5),
                    )
                    if distance > 4.5:
                        continue
                    path = self._pathfinder.find_path(start, standing, weight=weight)
                    if path:
                        choices.append((len(path), path, standing, support, face))
        if not choices:
            print(f"No reachable placement position for {(tx, ty, tz)}")
            return False

        _, path, standing, support, face = min(choices, key=lambda choice: choice[0])
        for x, y, z in path:
            self._executor.enque_command({"action": "move", "x": x, "y": y, "z": z})
        if source_slot < 36:
            self._executor.enque_command({
                "action": "swap_hotbar", "source_slot": source_slot,
                "hotbar_slot": hotbar_slot,
            })
        if hotbar_slot != inventory["selected_hotbar_slot"]:
            self._executor.enque_command({"action": "select_hotbar", "slot": hotbar_slot})

        dx = support[0] + 0.5 - (standing[0] + 0.5)
        dy = support[1] + 0.5 - (standing[1] + 1.62)
        dz = support[2] + 0.5 - (standing[2] + 0.5)
        self._executor.enque_command({
            "action": "look", "yaw": math.degrees(math.atan2(-dx, dz)),
            "pitch": math.degrees(-math.atan2(dy, math.hypot(dx, dz))),
        })
        self._executor.enque_command({
            "action": "place", "x": support[0], "y": support[1], "z": support[2],
            "face": face, "target": (tx, ty, tz), "block": block_name,
        })
        return True

    def attack_entity(self, entity_id):
        """Face and attack a tracked entity when it is within normal survival reach."""
        entity = self._world_state["entities"].get(int(entity_id))
        if entity is None:
            print(f"Entity {entity_id} is not currently tracked")
            return False
        position = self._world_state["position"]
        eye = (position["x"], position["y"] + 1.62, position["z"])
        target = (entity["x"], entity["y"] + 0.9, entity["z"])
        if math.dist(eye, target) > 3.0:
            print(f"Entity {entity_id} is outside attack reach")
            return False
        dx, dy, dz = (target[index] - eye[index] for index in range(3))
        self._executor.enque_command({
            "action": "look", "yaw": math.degrees(math.atan2(-dx, dz)),
            "pitch": math.degrees(-math.atan2(dy, math.hypot(dx, dz))),
        })
        self._executor.enque_command({"action": "swing", "hand": 0})
        self._executor.enque_command({"action": "attack", "entity_id": int(entity_id)})
        return True

    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Execution loop
    --------------------------------------------------------------------------------------------
    Runs on its own daemon thread, draining the command queue at 20 ticks per second to match
    Minecraft's expected packet rate. Started after connection is established so packets are
    never sent before the server is ready. Mirrors the listen thread pattern I created above
    exactly, best architectrue to achieve this, so it is safe to call on reconnect without 
    double starting.
    --------------------------------------------------------------------------------------------
    """

    def _start_execution(self):
        current = getattr(self, "_execution_thread", None)
        if not self._connection._connected or (current and current.is_alive()):
            return
        self._execution_thread = threading.Thread(target=self._execution_loop, daemon=True)
        self._execution_thread.start()
        self._execution_started = True

    def _execution_loop(self):
        while True:
            try:
                self._executor.execute_queue()
                time.sleep(0.05)

            except Exception as e:
                self._execution_started = False
                print(f"Execution error: {e}")
                break

        # ------------------------------------------------------------------------------------------

    """
    --------------------------------------------------------------------------------------------
    Function Header - prompt
    --------------------------------------------------------------------------------------------
    Public interface for guided mode. Takes a natural language prompt, passes it to the
    planner for a single shot API call, and enqueues the returned commands onto the executor.
    The execution loop picks them up automatically.
    --------------------------------------------------------------------------------------------
    """
    def prompt(self, user_prompt):
        commands = self._planner.plan(user_prompt)
        for cmd in commands:
            if cmd.get("action") in ("go_to", "find"):
                self.move_to((cmd["x"], cmd["y"], cmd["z"]))
            elif cmd.get("action") == "mine":
                self.mine_block((cmd["x"], cmd["y"], cmd["z"]))
            elif cmd.get("action") == "place":
                self.place_block(
                    (cmd["x"], cmd["y"], cmd["z"]), cmd["block"]
                )
            elif cmd.get("action") == "attack":
                self.attack_entity(cmd["entity_id"])
            else:
                self._executor.enque_command(cmd)

    """
    --------------------------------------------------------------------------------------------
    Function Field Header - run and injection handling 
    --------------------------------------------------------------------------------------------
    Public interface for autonomous mode. Takes a high level goal string and passes it to
    the planner agentic loop which reasons step by step until the goal is complete or
    max_steps is reached. Commands are enqueued directly by the planner loop.
    
    plan_loop runs on the thread, and each time it completes a step it calls s
    elf._on_step(resolved) which enqueues the commands. The execution thread then independently 
    drains the command queue at 20tps and sends the packet
    --------------------------------------------------------------------------------------------
    """
    def _on_step(self, commands):
        result_start = self._executor.result_count()
        planning_results = []
        for cmd in commands:
            if cmd.get("action") in ("go_to", "find"):
                if not self.move_to((cmd["x"], cmd["y"], cmd["z"])):
                    planning_results.append(f"No path to {(cmd['x'], cmd['y'], cmd['z'])}")
            elif cmd.get("action") == "mine":
                if not self.mine_block((cmd["x"], cmd["y"], cmd["z"])):
                    planning_results.append(f"Could not plan mining at {(cmd['x'], cmd['y'], cmd['z'])}")
            elif cmd.get("action") == "place":
                if not self.place_block(
                    (cmd["x"], cmd["y"], cmd["z"]), cmd["block"]
                ):
                    planning_results.append(
                        f"Could not plan placing {cmd['block']} at {(cmd['x'], cmd['y'], cmd['z'])}"
                    )
            elif cmd.get("action") == "attack":
                if not self.attack_entity(cmd["entity_id"]):
                    planning_results.append(f"Could not attack entity {cmd['entity_id']}")
            else:
                self._executor.enque_command(cmd)
        results = self._executor.wait_until_idle(result_start=result_start)
        summaries = planning_results + [
            ("Succeeded: " if result["success"] else "Failed: ") + result["message"]
            for result in results
        ]
        return "; ".join(summaries) or "No actions were queued"

    def run(self, goal, max_steps=20):
        self._run_thread = threading.Thread(
            target=self._planner.plan_loop,
            args=(goal,),
            kwargs={"on_step": self._on_step, "max_steps": max_steps},
            daemon=True
        )
        self._run_thread.start()

    def inject(self, prompt):
        # injects a mid-task prompt into the autonomous loop while it is running
        self._planner.inject(prompt)

    def stop_run(self):
        # signals the autonomous loop to stop after the current step completes
        # by injecting a stop signal into the planner history
        self._planner.inject("Stop the current task immediately. Return [].")

        # ------------------------------------------------------------------------------------------

    """
    --------------------------------------------------------------------------------------------
    Function Header - Update field with a validation layer built into it
    --------------------------------------------------------------------------------------------
    Generic updater for any configurable field with built in validation layer. Validates the 
    value and falls back to default if invalid. Designed seperately from the initialization 
    validation layer as a design choice.
    --------------------------------------------------------------------------------------------
    """
    def set(self, key, value):
        while key not in self.default_values:
            key = input(f"'{key}' is not a valid field. Enter a valid key: ")

        if value is None:
            value = input(f"Enter new value for '{key}': ")

        allowed = self.allowed_values.get(key)

        if allowed is None:
            is_valid = value is not None
        else:
            is_valid = value in allowed

        self._valid_flags[key] = is_valid

        if not is_valid:
            print(f"Invalid key: '{key}', using default: {self.default_values[key]}")
            value = self.default_values[key]
            is_valid = True
            self._valid_flags[key] = is_valid

        setattr(self, f"_{key}", value)

    """
    --------------------------------------------------------------------------------------------
    Function Header - Failure Handling
    --------------------------------------------------------------------------------------------
    define a general handler in Bot that takes the exception and decides what to do based on 
    it's type. Everytime there is a connection error that propagates to this function, we pass 
    e and try to connect again, with a loop of connection attempts (3 iterations). Same for
    execution thread however:
    
     he break exits the while True loop which returns from _execution_loop, ending the thread 
     naturally. The thread function returning is what terminates the thread in Python, there's 
     no explicit thread stop needed. In connection we do it explicitely because _listen was 
     written with an explicit boolean flag b before you had the execution loop as a reference. 
     The b = False pattern is slightly more verbose but functionally identical to break.
    --------------------------------------------------------------------------------------------
    """
    def _handle_failure(self, e):
        if isinstance(e, ConnectionError):
            print(f"Connection failure: {e}, attempting reconnect (3 attempts before "
                  f"system shutdown")

            i = 1
            while i <= 3:
                try:
                    self._connection.connect()
                    self._start_execution()
                    break

                except Exception as e:

                    if isinstance(e, ConnectionError):
                        print(f"protocol error: {e}.\nDISCONNECTING.")
                        self._connection.disconnect()

                    # bad data from server, maybe log and disconnect cleanly
                    elif isinstance(e, ValueError):
                        print(f"Protocol error: {e}.\nDISCONNECTING.")
                        self._connection.disconnect()

                    else:
                        print(f"Unexpected error: {e}, shutting down")
                        self._connection.disconnect()

                    if (i == 3):
                        break

                i += 1

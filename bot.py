"""Public AMP façade that composes transport, world state, gameplay, planning, and execution."""
# imports
import os
import threading
import time
from connection import Connection as _Connection
from execution import Execute
from gameplay import GameplayController
from pathfinder import Pathfinder
from planner import Planner
from protocol_data import packet_ids_for_protocol, version_protocols
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
        self._connection = _Connection(
            self._host, self._port, self._version, self._username,
            on_failure=self._handle_failure, protocol_version=protocol,
        )
        self._world_tracker = WorldStateTracker(
            self._version, self._connection, self.play_ids
        )
        self._world_state = self._world_tracker.state
        self._connection._packet_handler = self._world_tracker._on_packet
        self._input_mode = None
        self._pathfinder = Pathfinder(self._world_state)
        self._executor = Execute(
            self._connection,
            game_mode=config.get("game_mode", "survival"),
            behavior_mode=config.get("behavior_mode", "neutral"),
            world_state=self._world_state,
        )
        self._gameplay = GameplayController(
            self._world_state, self._pathfinder, self._executor,
            self._version, self._game_mode,
        )
        self._execution_started = False
        self._execution_thread = None
        # Load local development credentials without overriding environment variables
        # supplied by a shell, CI runner, or deployment platform.
        load_dotenv()
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("Warning: ANTHROPIC_API_KEY not found, planner will not function")

        self._planner = Planner(
            self._world_state,
            api_key,
            model=os.environ.get("ANTHROPIC_MODEL"),
        )
        self._run_thread = None

    def move_to(self, goal):
        return self._gameplay.move_to(goal)

    def mine_block(self, target):
        return self._gameplay.mine_block(target)

    def place_block(self, target, block_name):
        return self._gameplay.place_block(target, block_name)

    def attack_entity(self, entity_id):
        return self._gameplay.attack_entity(entity_id)

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
        self._gameplay.set_mode(mode)

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

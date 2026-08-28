"""Public AMP façade that composes transport, world state, gameplay, planning, and execution."""
# imports
import os
import threading
from amp import connection
from amp.execution import Execute
from amp.gameplay import GameplayController
from amp.lifecycle import LifecycleManager
from amp.java26_protocol import Java26ProtocolAdapter
from amp.model_clients import build_model_client
from amp.pathfinder import Pathfinder
from amp.planner import Planner
from amp.protocol_adapters import ProtocolAdapterRegistry
from amp.protocol_data import packet_ids_for_protocol
from amp.version_support import load_support_manifest, pending_versions, runnable_version_protocols
from amp.world_state import WorldStateTracker
from dotenv import find_dotenv, load_dotenv

"""
--------------------------------------------------------------------------------------------
Class Header - Bot initialization
--------------------------------------------------------------------------------------------
"""
class Bot:
    version_protocol = runnable_version_protocols()
    pending_versions = frozenset(pending_versions())

    """
    --------------------------------------------------------------------------------------------
    Function Header - Constants field
    --------------------------------------------------------------------------------------------
    Within Bot to avoid duplication of constants for each Bot object. Explicitely we are saying 
    username/host has no restricted range of allowed possibilities (same as saying "username": 
    None ... etc.).
    --------------------------------------------------------------------------------------------
    """
    allowed_values = {"game_mode": {"survival", "creative"},
                      "port": range(1024, 65536),
                      "version": set(version_protocol)}

    default_values = {"host": "localhost", "port": 25565, "username": "Guest", "version": "26.2",
        "game_mode": "survival"}

    """
    --------------------------------------------------------------------------------------------
    Function Header - Version to protocol map
    --------------------------------------------------------------------------------------------
    The handshake sends a protocol number, not a version string, and every packet ID is keyed
    to that number.
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
            if key == "version" and value is not None and value not in allowed:
                if value in self.pending_versions:
                    raise ValueError(
                        f"Minecraft version '{value}' is pending protocol validation"
                    )
                raise ValueError(
                    f"Unsupported Minecraft version: {value!r}; "
                    f"runnable versions: {sorted(allowed)}"
                )
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
        self._valid_flags = {}
        # guarantee the object is always in a valid state immediately after
        # creation with config get
        self._validate_input()
        protocol = self.version_protocol[self._version]
        self.play_ids = packet_ids_for_protocol(protocol, "clientbound")
        self._connection = connection.Connection(
            self._host, self._port, self._version, self._username,
            on_failure=lambda error: self._lifecycle.handle_failure(error),
            protocol_version=protocol,
            auth_session=config.get("auth_session"),
            session_joiner=config.get("session_joiner"),
        )
        adapter_registry = ProtocolAdapterRegistry()
        manifest = load_support_manifest()
        adapter = Java26ProtocolAdapter(
            manifest["versions"][self._version]["family"],
            self._version,
            self._connection,
        )
        adapter_registry.register(adapter)
        self._protocol_adapter = adapter_registry.for_version(self._version)
        self._connection.set_protocol_adapter(self._protocol_adapter)
        self._world_tracker = WorldStateTracker(
            self._protocol_adapter, self._connection
        )
        self._world_state = self._world_tracker.state
        self._connection._packet_handler = self._world_tracker._on_packet
        self._input_mode = None
        self._pathfinder = Pathfinder(self._world_state)
        self._executor = Execute(
            self._connection,
            game_mode=self._game_mode,
            protocol_adapter=self._protocol_adapter,
            world_state=self._world_state,
        )
        self._gameplay = GameplayController(
            self._world_state, self._pathfinder, self._executor,
            self._version, self._game_mode,
        )
        self._lifecycle = LifecycleManager(
            self._connection,
            self._executor,
            (self._username, self._host, self._port),
        )
        # Load local development credentials without overriding environment variables
        # supplied by a shell, CI runner, or deployment platform. Search from the
        # working directory: the package lives in site-packages once installed.
        load_dotenv(find_dotenv(usecwd=True))
        model_client = build_model_client(os.environ)
        if model_client is None and not config.get("model_optional", False):
            print("Warning: model provider is not configured, planner will not function")

        self._planner = Planner(self._world_state, model_client)
        self._run_thread = None

    def move_to(self, goal):
        return self._gameplay.move_to(goal)

    def mine_block(self, target):
        return self._gameplay.mine_block(target)

    def mine_nearest(self, block_name, radius=8):
        return self._gameplay.mine_nearest(block_name, radius)

    def place_block(self, target, block_name):
        return self._gameplay.place_block(target, block_name)

    def attack_entity(self, entity_id):
        return self._gameplay.attack_entity(entity_id)

    # entrance for cli
    def start(self):
        if not all(self._valid_flags.values()):
            invalid = [k for k, v in self._valid_flags.items() if not v]
            print(f"Warning: fields fell back to defaults: {invalid}")

        self._lifecycle.start()

    def disconnect(self):
        """Disconnect through the Bot lifecycle boundary."""
        self._lifecycle.disconnect()

    def set_mode(self, mode):
        self._input_mode = mode
        self._gameplay.set_mode(mode)

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
        return self._on_step(commands)

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
            if cmd.get("action") == "go_to":
                if not self.move_to((cmd["x"], cmd["y"], cmd["z"])):
                    planning_results.append(f"No path to {(cmd['x'], cmd['y'], cmd['z'])}")
            elif cmd.get("action") == "mine_nearest":
                if not self.mine_nearest(cmd["block"], cmd["radius"]):
                    planning_results.append(
                        f"Could not find a reachable {cmd['block']}"
                    )
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

        if key == "version" and value not in allowed:
            if value in self.pending_versions:
                raise ValueError(
                    f"Minecraft version '{value}' is pending protocol validation"
                )
            raise ValueError(
                f"Unsupported Minecraft version: {value!r}; "
                f"runnable versions: {sorted(allowed)}"
            )

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

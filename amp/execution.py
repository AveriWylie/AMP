"""
--------------------------------------------------------------------------------------------
Execution Module - Wrapper for packet sending through acces to bot
--------------------------------------------------------------------------------------------
Translates structured commands from the command queue into raw Minecraft protocol packets
and sends them over the connection. Single entry point is execute_queue() which drains
the queue and dispatches each command.

Every command enqueued is a dict with an action key and any additional fields the action
needs, for example {"action": "move", "x": x, "y": y, "z": z} or {"action": "mine",
"x": x, "y": y, "z": z, "face": 1}. _execute pulls the action key to dispatch and then
reads the remaining fields by name.

--------------------------------------------------------------------------------------------
"""
# imports
import threading
from collections import deque
import time
from amp.command_data import EXECUTOR_ACTIONS
from amp.protocol_types import action_from_command

"""
--------------------------------------------------------------------------------------------
Class Header - Execution layer
--------------------------------------------------------------------------------------------
execution_loop runs on its own thread calling execute_queue every 0.05s, execute_queue 
drains the command queue and sends packets via _connection._send. Nothing needs rewiring, 
the thread drives execution automatically once _start_execution is called in start().
--------------------------------------------------------------------------------------------
"""
class Execute:
    def __init__(self, connection, game_mode, protocol_adapter, world_state=None):
        self._connection = connection
        self._command_queue = deque()
        self._game_mode = game_mode
        self._protocol_adapter = protocol_adapter
        self._world_state = world_state
        self._condition = threading.Condition()
        self._active = False
        self._results = []

    """
    --------------------------------------------------------------------------------------------
    Function Header - Bot methods field
    --------------------------------------------------------------------------------------------
    Using command queue we order the commands, using popleft to execute in FIFO order.
    
    execution queue is a key producing wrapper for the packets we send in execute
    --------------------------------------------------------------------------------------------
    """
    def enque_command(self, command):
        with self._condition:
            self._command_queue.append(command)
            self._condition.notify_all()

    def result_count(self):
        with self._condition:
            return len(self._results)

    def wait_until_idle(self, result_start=0, timeout=30):
        """Wait for queued and active work, returning results added since result_start."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._command_queue or self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._results[result_start:] + [{
                        "action": "batch", "success": False,
                        "message": "Timed out waiting for queued actions",
                    }]
                self._condition.wait(remaining)
            return list(self._results[result_start:])

    def execute_queue(self):
        """Execute at most one command; the Bot loop calls this once per 50 ms tick."""
        with self._condition:
            if not self._command_queue:
                return None
            command = self._command_queue.popleft()
            self._active = True
        try:
            result = self._execute(command)
        except Exception as error:
            result = {
                "action": command.get("action"), "success": False,
                "message": f"{type(error).__name__}: {error}",
            }
            raise
        finally:
            with self._condition:
                self._results.append(result)
                self._active = False
                self._condition.notify_all()
        return result

    def _world_block(self, position):
        if self._world_state is None:
            return None
        x, y, z = position
        chunk = self._world_state["map"].get((x >> 4, z >> 4))
        return chunk.get_block(x, y, z) if chunk else None

    def _wait_for_block(self, position, expected, timeout=2):
        if self._world_state is None:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            block = self._world_block(position)
            if expected(block):
                return True
            time.sleep(0.05)
        return False

    def _execute(self, command):
        action = command.get("action")
        if action not in EXECUTOR_ACTIONS:
            raise ValueError(f"Unsupported action: {action!r}")
        action_value = action_from_command(command)
        encoded = self._protocol_adapter.encode_action(
            action_value, self._world_state, self._game_mode
        )
        movement_revision = None
        previous_position = None
        if action == "move" and self._world_state is not None:
            movement_revision = self._world_state.get("position_revision", 0)
            previous_position = dict(self._world_state["position"])
            self._world_state["position"].update({
                "x": command["x"], "y": command["y"], "z": command["z"]
            })
        try:
            for step in encoded.steps:
                if step.delay_before:
                    time.sleep(step.delay_before)
                self._connection._send(step.packet)
        except Exception:
            if (
                previous_position is not None
                and self._world_state.get("position_revision", 0)
                == movement_revision
            ):
                self._world_state["position"] = previous_position
            raise
        success = True
        message = f"{action} packet sent"

        if action == "move":
            time.sleep(0.25)
            if (
                self._world_state is not None
                and self._world_state.get("position_revision", 0)
                != movement_revision
            ):
                success = False
                message = "Server corrected movement; cancelled stale actions"
                with self._condition:
                    self._command_queue.clear()
                    self._condition.notify_all()

        elif action == "attack":
            message = f"Attack sent to entity {command['entity_id']}"

        elif action == "mine":
            x, y, z = command["x"], command["y"], command["z"]
            success = self._wait_for_block(
                (x, y, z), lambda block: block in ("air", "cave_air", "void_air")
            )
            message = (
                f"Mined block at {(x, y, z)}" if success
                else f"Block at {(x, y, z)} did not disappear"
            )

        elif action == "place":
            x, y, z = command["x"], command["y"], command["z"]
            target = tuple(command.get("target", ()))
            if len(target) == 3 and command.get("block"):
                success = self._wait_for_block(
                    target, lambda block: block == command["block"]
                )
                message = (
                    f"Placed {command['block']} at {target}" if success
                    else f"Expected {command['block']} did not appear at {target}"
                )

        elif action == "select_hotbar":
            slot = command["slot"]
            if self._world_state is not None:
                self._world_state["inventory"]["selected_hotbar_slot"] = slot

        print(f"Executed {command} in {self._game_mode} mode.")
        return {"action": action, "success": success, "message": message}

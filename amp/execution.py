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

Because the loop thread and the planner thread both touch this, one Condition guards all of
the shared state, the queue, the active flag and the results list. It is a Condition rather
than a Lock because wait_until_idle needs to sleep until something changes, and polling a
lock in a loop would either burn cycles or add latency to every action.

_active exists separately from the queue because a command that has been popped is still in
flight. Without it, "queue is empty" would read as idle while a packet was mid-send, and the
planner would replan on top of an action that had not landed.
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
    Function Field Header - Bot methods
    --------------------------------------------------------------------------------------------
    Using command queue we order the commands, using popleft to execute in FIFO order.

    execution queue is a key producing wrapper for the packets we send in execute

    notify_all rather than notify because more than one waiter can be parked here, and waking
    the wrong single one would leave the other blocked until the next command happened to
    arrive.
    --------------------------------------------------------------------------------------------
    """
    def enque_command(self, command):
        with self._condition:
            self._command_queue.append(command)
            self._condition.notify_all()


    # Callers take this before enqueuing, then pass it to wait_until_idle, so they read back
    # only the results their own batch produced rather than everything since startup.
    def result_count(self):
        with self._condition:
            return len(self._results)


    def cancel_pending(self):
        with self._condition:
            self._command_queue.clear()
            self._condition.notify_all()


    # Closes a client tick. Sent every tick whether or not anything was executed, because the
    # boundary is what the server uses to group and time the tick's actions, so skipping it on
    # an idle tick would make the bot's timing look wrong rather than merely quiet.
    def end_tick(self):
        self._connection._send(self._protocol_adapter.encode_tick_end())


    """
    --------------------------------------------------------------------------------------------
    Function Header - Wait until idle
    --------------------------------------------------------------------------------------------
    Blocks until the queue has drained and nothing is in flight, then returns the results added
    since result_start. This is what makes the planner synchronous with execution, it plans,
    waits for the batch to actually land, then plans again against a world that has moved.

    The deadline is computed once rather than per wait, so the timeout covers the whole call
    instead of resetting on every notification. A stream of unrelated notifications would
    otherwise keep extending it forever.

    On timeout it returns the partial results plus a synthetic failure entry rather than
    raising. The actions that did complete still happened and the caller needs to see them, and
    a timeout is a normal outcome when the server is slow rather than an exceptional one.
    --------------------------------------------------------------------------------------------
    """
    def wait_until_idle(self, result_start=0, timeout=30):
        deadline = time.monotonic() + timeout

        with self._condition:
            while self._command_queue or self._active:
                # recomputed each pass, the deadline is for the whole call not each wait
                remaining = deadline - time.monotonic()

                if remaining <= 0:
                    return (self._results[result_start:] +
                        [{"action": "batch", "success": False,"message": "Timed out waiting for queued actions", }])

                self._condition.wait(remaining)

            return list(self._results[result_start:])

    """
    --------------------------------------------------------------------------------------------
    Function Header - Execute queue
    --------------------------------------------------------------------------------------------
    Takes at most one command per call, because the caller is a 50 ms tick loop and draining
    the whole queue in one pass would hold the connection for as long as the queue is long,
    with no chance to react to anything the server said in between.

    The lock is released before _execute runs. Sending packets and waiting for block changes
    can take seconds, and holding the condition across that would block anything trying to
    enqueue or check progress for the whole duration.

    The finally block records the result and clears _active whatever happens, including on the
    re-raise path, so a failure cannot leave _active stuck True and wait_until_idle blocked
    until its timeout.
    --------------------------------------------------------------------------------------------
    """
    def execute_queue(self):

        with self._condition:

            if not self._command_queue:
                return None

            command = self._command_queue.popleft()
            self._active = True

        # deliberately outside the lock, sending can take seconds and would block enqueuers
        try:
            result = self._execute(command)
        except Exception as error:
            result = {"action": command.get("action"), "success": False, "message": f"{type(error).__name__}: {error}",}
            raise
        # runs on both paths, so a failure cannot leave _active stuck and waiters parked
        finally:
            with self._condition:
                self._results.append(result)
                self._active = False
                self._condition.notify_all()

        return result

    """
    --------------------------------------------------------------------------------------------
    Function Field Header - World observation helpers
    --------------------------------------------------------------------------------------------
    Confirmation helpers. Sending a packet only proves it left, not that the server accepted
    it, so mining and placing poll world state until the change actually shows up.

    Both return the permissive answer when there is no world state to consult. Without a
    tracker there is nothing to confirm against, and treating that as failure would make every
    action report failure in tests and in headless use.

    Polling rather than waiting on an event because block updates arrive through the packet
    thread into a plain dict, with no signal to subscribe to. 0.05s matches the execution tick,
    so it costs at most one tick of latency.
    --------------------------------------------------------------------------------------------
    """
    def _world_block(self, position):

        if self._world_state is None:
            return None

        x, y, z = position
        chunk = self._world_state["map"].get((x >> 4, z >> 4))

        return chunk.get_block(x, y, z) if chunk else None

    def _wait_for_block(self, position, expected, timeout=2):

        # nothing to confirm against, so assume success rather than failing every action
        if self._world_state is None:
            return True

        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            block = self._world_block(position)

            if expected(block):
                return True

            time.sleep(0.05)

        return False

    """
    --------------------------------------------------------------------------------------------
    Function Header - Execute
    --------------------------------------------------------------------------------------------
    Encodes one command and sends its packets, then confirms the result per action type.

    Movement is the awkward one. The position is updated optimistically before the packets go
    out, because the server expects movement to be continuous and the next action needs to
    encode from where we claim to be. position_revision is captured first so that if sending
    fails, the optimistic write can be rolled back, but only when nothing else has moved us in
    the meantime, otherwise the rollback would undo a real server correction.

    After a move it sleeps briefly then re-checks the revision. A change means the server
    rejected the position and teleported us back, which invalidates every queued action planned
    from the old position, so the queue is cleared rather than continuing to walk a dead path.

    Confirmation differs per action because what counts as done differs. Mining is done when
    the block turns to air, placing when the expected block appears, attacking is fire and
    forget since damage is not observable from here. Actions with no observable effect simply
    report that the packet went out, which is the honest claim.
    --------------------------------------------------------------------------------------------
    """
    def _execute(self, command):
        action = command.get("action")

        if action not in EXECUTOR_ACTIONS:
            raise ValueError(f"Unsupported action: {action!r}")

        action_value = action_from_command(command)
        encoded = self._protocol_adapter.encode_action(action_value, self._world_state, self._game_mode)
        movement_revision = None
        previous_position = None

        # optimistic write, the next action has to encode from where we claim to be
        if action == "move" and self._world_state is not None:
            movement_revision = self._world_state.get("position_revision", 0)
            previous_position = dict(self._world_state["position"])
            self._world_state["position"].update({"x": command["x"], "y": command["y"], "z": command["z"]})

        try:
            for step in encoded.steps:
                # some packets have to be spaced, the server drops them if they arrive together
                if step.delay_before:
                    time.sleep(step.delay_before)

                self._connection._send(step.packet)

        except Exception:
            # only roll back if nothing else moved us, otherwise this would undo a correction
            if (previous_position is not None and self._world_state.get("position_revision", 0) == movement_revision):
                self._world_state["position"] = previous_position

            raise

        success = True
        message = f"{action} packet sent"

        if action == "move":
            # overridable because a physics step is a fraction of a tick while a planned walk
            # step needs long enough for the server to answer with a correction
            time.sleep(command.get("delay", 0.25))

            # revision changed means the server teleported us back, so the plan is stale
            if (self._world_state is not None and self._world_state.get("position_revision", 0) != movement_revision):
                success = False
                message = "Server corrected movement; cancelled stale actions"
                with self._condition:
                    self._command_queue.clear()
                    self._condition.notify_all()

        # damage is not observable from here, so sending is all that can be claimed
        elif action == "attack":
            message = f"Attack sent to entity {command['entity_id']}"

        # done when the block is gone, all three air variants count
        elif action == "mine":
            x, y, z = command["x"], command["y"], command["z"]
            success = self._wait_for_block((x, y, z), lambda block: block in ("air", "cave_air", "void_air"))
            message = (f"Mined block at {(x, y, z)}" if success else f"Block at {(x, y, z)} did not disappear")

        # only confirmable when the caller said which block should appear where
        elif action == "place":
            x, y, z = command["x"], command["y"], command["z"]
            target = tuple(command.get("target", ()))
            if len(target) == 3 and command.get("block"):
                success = self._wait_for_block(target, lambda block: block == command["block"])
                message = (f"Placed {command['block']} at {target}" if success else
                           f"Expected {command['block']} did not appear at {target}")

        # mirrored locally so later actions encode against the slot we just selected
        elif action == "select_hotbar":
            slot = command["slot"]
            if self._world_state is not None:
                self._world_state["inventory"]["selected_hotbar_slot"] = slot

        # report=False is for internally generated commands, gravity steps mostly, which would
        # otherwise flood the log at 20 a second and bury anything the planner actually did
        if success:
            if command.get("report", True):
                print(f"Executed {command} in {self._game_mode} mode.")

        # failures always print, a silent failure is the thing worth knowing about
        else:
            print(f"Failed {command}: {message}")

        result = {"action": action, "success": success, "message": message}

        # marked so the planner can tell its own actions apart from physics that just happened
        if success and not command.get("report", True):
            result["internal"] = True

        return result

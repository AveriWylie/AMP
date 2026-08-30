"""
--------------------------------------------------------------------------------------------
Planner Module
--------------------------------------------------------------------------------------------
AI reasoning layer that sits between CLI input and the command queue. Takes a natural
language prompt and a snapshot of world state, calls the configured model, and returns a list
of structured commands that bot feeds into the executor.

The two input modes differ here in a meaningful way:

Guided mode: single intent, one API call, returns a command list, executes, waits for the
next user prompt. Deterministic and user-driven.

Autonomous mode: agentic loop where each completed step and its result feed as context
into the next API call. The planner reasons over what it just did and what to do next
until the high level goal is complete or max steps is reached.

Commands returned by the planner are dicts with an action key. Low level actions (move,
chat) go directly to the executor. High level intents (mine, place, attack) are resolved
by the planner itself using the pathfinder before being handed to the executor as move
commands.

The world state passed to the API is a concise snapshot, not raw chunk objects. Position,
health, food, nearby surface blocks sampled from loaded chunks, and entity positions. This
gives the AI genuine spatial grounding without overwhelming the context window.
--------------------------------------------------------------------------------------------
"""
# imports
import json
import math
import threading
import queue
from amp.command_data import planner_command_error
from amp.model_clients import ModelClientError

"""
--------------------------------------------------------------------------------------------
Class Header - Planner
--------------------------------------------------------------------------------------------
Takes world_state and pathfinder by reference so it always reasons over live data.
The provider client is supplied by the composition root.
--------------------------------------------------------------------------------------------
"""
class Planner:

    MAX_TOKENS = 1024
    # commands the executor handles directly, no planner resolution needed
    LOW_LEVEL_ACTIONS = {"chat", "look"}
    # commands the planner resolves into move sequences before passing to executor
    HIGH_LEVEL_ACTIONS = {"go_to", "mine_nearest", "mine", "place", "attack", "kill"}


    def __init__(self, world_state, model_client=None):
        self._world_state = world_state
        self._model_client = model_client
        # conversation history for autonomous agentic loop
        self._history = []
        # thread-safe queue for mid-task prompt injection in autonomous mode
        self._inject_queue = queue.Queue()
        # set from the CLI thread to end an autonomous run, checked at each point the loop
        # could otherwise start work the user has already asked it to abandon
        self._stop_event = threading.Event()


    # Cleared on a new run rather than at the end of the last one, so a stop stays in force
    # until something deliberately starts again.
    def reset_stop(self):
        self._stop_event.clear()


    def stop(self):
        self._stop_event.set()


    """
    --------------------------------------------------------------------------------------------
    Function Header - World state snapshot
    --------------------------------------------------------------------------------------------
    Builds a concise JSON-serializable summary of world state for the API context.
    Raw chunk objects are not serializable and are far too large for the context window.
    Instead we sample surface blocks in a radius around the bot's current position using
    get_surface_y and get_block so the AI has genuine spatial grounding.

    Sampling radius of 8 blocks gives a 17x17 column footprint around the bot, enough
    to reason about immediate surroundings without flooding the context.
    --------------------------------------------------------------------------------------------
    """
    def _build_snapshot(self, radius=8):
        pos = self._world_state["position"]
        bx = math.floor(pos["x"])
        by = math.floor(pos["y"])
        bz = math.floor(pos["z"])

        nearby = {}

        # one sample per column rather than per block, 289 entries instead of ~100k
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                wx = bx + dx
                wz = bz + dz
                cx = wx >> 4
                cz = wz >> 4
                chunk = self._world_state["map"].get((cx, cz))

                # unloaded column, skipped rather than reported, the model cannot act on it
                if chunk is None:
                    continue

                sy = chunk.get_surface_y(wx, wz)

                # None means an empty column, so there is no surface block to name
                if sy is not None:
                    block = chunk.get_block(wx, sy, wz)
                    # string key because this is going through json, which has no tuple keys
                    nearby[f"{wx},{sy},{wz}"] = block

        yaw = pos.get("yaw", 0.0)
        directions = ("south", "west", "north", "east")
        facing = directions[round(yaw / 90) % 4]

        return {
            "position": {
                "x": bx, "y": by, "z": bz,
                "yaw": yaw, "pitch": pos.get("pitch", 0.0),
                "facing": facing,
            },
            "health": self._world_state["health"],
            "food": self._world_state["food"],
            "inventory": {"selected_hotbar_slot": self._world_state.get("inventory", {}).get("selected_hotbar_slot", 0),
                "slots": {str(slot): {"name": item["name"], "count": item["count"]}
                          for slot, item in self._world_state.get("inventory", {}).get("slots", {}).items()
                },
            },
            "nearby_surface_blocks": nearby,
            "entities": {
                str(eid): {"type": e["type"], "name": e.get("name", f"entity_{e['type']}"), "x": int(e["x"]), "y": int(e["y"]), "z": int(e["z"])}
                for eid, e in self._world_state["entities"].items()
            },
        }


    """
    --------------------------------------------------------------------------------------------
    Function Header - API call
    --------------------------------------------------------------------------------------------
    Sends the conversation history plus the current user message to the configured model.
    System prompt grounds the model in its role and defines the exact JSON output format.
    The model must return only a JSON array of command objects and nothing else so the
    response can be parsed directly without stripping markdown fences.

    Each command object has at minimum an action key. Additional keys depend on action:
    move:    x, y, z (ints)
    chat:    message (string)
    go_to:   x, y, z (ints) - resolved by planner into move sequence
    mine_nearest: block (string), radius (int) - searches loaded 3D block data
    mine:    x, y, z (ints) - resolved by planner into go_to + mine action
    place:   x, y, z (ints), block (string) - resolved by planner into go_to + place action
    --------------------------------------------------------------------------------------------
    """
    def _call_api(self, user_message):

        # idle mode runs with no provider, so this returns an empty plan rather than raising
        if self._model_client is None:
            print("Planner unavailable: configure a model provider")
            return "[]"

        system = (
            "You are the AI brain of a Minecraft bot with genuine spatial awareness. "
            "You receive a snapshot of the bot's world state and a natural language instruction. "
            "You must respond with ONLY a valid JSON array of command objects and nothing else. "
            "No explanation, no markdown, no preamble. Just the raw JSON array.\n\n"
            "Available actions:\n"
            "  {\"action\": \"chat\", \"message\": string}\n"
            "  {\"action\": \"look\", \"yaw\": number, \"pitch\": number}\n"
            "  {\"action\": \"go_to\", \"x\": int, \"y\": int, \"z\": int}\n"
            "  {\"action\": \"mine_nearest\", \"block\": string, \"radius\": int}\n"
            "  {\"action\": \"mine\", \"x\": int, \"y\": int, \"z\": int}\n"
            "  {\"action\": \"place\", \"x\": int, \"y\": int, \"z\": int, \"block\": string}\n\n"
            "  {\"action\": \"attack\", \"entity_id\": int}\n\n"
            "  {\"action\": \"kill\", \"entity_id\": int}\n\n"
            "Use the world state snapshot to ground your decisions in real coordinates. "
            "Prefer go_to over raw move sequences. To find and mine a nearby block, use "
            "mine_nearest. Use {\"block\": \"log\"} only when any tree species is "
            "acceptable. Preserve a requested species with its exact block name, such as "
            "{\"block\": \"dark_oak_log\"} for dark oak. "
            "Never return an empty array for a guided instruction. If the requested "
            "action cannot be performed, return one chat command that clearly explains "
            "why. Return [] only when an autonomous prompt explicitly says its goal is "
            "complete. "
            "Use attack for one hit. Use kill when asked to kill or defeat an entity; "
            "kill approaches the target and attacks until it dies. "
            "Keep command lists concise and purposeful."
        )

        self._history.append({
            "role": "user",
            "content": user_message
        })

        try:
            reply = self._model_client.complete(system, list(self._history), self.MAX_TOKENS)
        except ModelClientError as error:
            # drop the message we just appended, a failed call leaves no reply to pair it with
            # and an unanswered user turn would corrupt the alternation on the next request
            self._history.pop()
            print(f"Planner model error: {error}")
            return "[]"

        self._history.append({"role": "assistant","content": reply})
        return reply


    """
    --------------------------------------------------------------------------------------------
    Function Header - Parse commands
    --------------------------------------------------------------------------------------------
    Parses the raw API response string into a list of command dicts. Strips json fences if
    the model includes them despite instructions not to. Returns empty list on parse failure
    rather than crashing so the bot degrades gracefully.

    Model output is untrusted input, which is the whole reason this is defensive. The system
    prompt asks for a bare JSON array, but a model can wrap it in fences, append an
    explanation, or return an object instead of a list, and none of those should stop the bot.

    raw_decode rather than loads because it parses one value and reports where it stopped, so
    trailing prose after a valid array can be noticed and ignored instead of failing the whole
    reply.

    Every command is then validated individually by planner_command_error. One bad command is
    dropped with a reason printed while the rest still run, since discarding an entire plan
    because of one malformed entry loses work the model got right.
    --------------------------------------------------------------------------------------------
    """
    @staticmethod
    def _parse_commands(raw):
        clean = raw.strip()

        # fences appear despite the system prompt forbidding them, so strip rather than fail
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1]
            clean = clean.rsplit("```", 1)[0]

        # a reply cut off at max_tokens loses its closing bracket, and one bracket is a cheap
        # thing to try before throwing away a plan that is otherwise complete
        candidates = [clean]

        if clean.startswith("[") and not clean.endswith("]"):
            candidates.append(clean + "]")

        commands = None
        end = 0

        # raw_decode reports where parsing stopped, so trailing prose is tolerated either way
        for candidate in candidates:
            try:
                commands, end = json.JSONDecoder().raw_decode(candidate)
                clean = candidate
                break
            except json.JSONDecodeError:
                continue

        if commands is None:
            print(f"Planner parse error: {raw}")
            return []

        try:
            # an object or a bare string is not a plan, so there is nothing to salvage
            if not isinstance(commands, list):
                return []

            valid = []

            # per-command validation, one bad entry should not discard the whole plan
            for command in commands:
                error = planner_command_error(command)

                if error:
                    print(f"Planner command rejected: {error}")
                else:
                    valid.append(command)

            return valid

        # the JSON parsed, so anything failing now is a malformed command inside it
        except (TypeError, ValueError):
            print(f"Planner parse error: {raw}")
            return []


    """
    --------------------------------------------------------------------------------------------
    Function Header - Resolve high level commands
    --------------------------------------------------------------------------------------------
    High level actions like go_to cannot be sent directly to the executor since the
    executor only knows about move and chat packets. This method resolves them into sequences
    of low level move commands using the pathfinder.

    go_to: calls pathfinder.find_path from current position to target, expands into moves
    mine_nearest remains high-level for Bot.mine_nearest(), which searches all loaded
    blocks in its radius before selecting a reachable target. Mine remains a high-level
    command for Bot.mine_block(), which selects a reachable adjacent
    standing position before enqueueing the interaction. Place remains high-level for
    Bot.place_block(), which selects inventory and a valid support face.

    Every branch now returns the command untouched, so this is a passthrough. That is not an
    oversight, it is where the expansion used to happen before it moved down into gameplay,
    which is the better place for it since that layer already owns reach, standing positions
    and inventory. What is left is the seam, kept explicit so the dispatch in plan and
    plan_loop still reads as low-level versus high-level rather than everything going one way.

    The branches are worth keeping for the same reason. They name which actions are high-level,
    which is documentation the set above cannot express on its own, and they are where
    per-action handling goes if any of it ever needs to happen before gameplay sees it.
    --------------------------------------------------------------------------------------------
    """
    def _resolve(self, command, snapshot):
        action = command.get("action")

        # all four pass through, resolution lives in gameplay now, the branches mark the seam
        if action == "go_to":
            return [command]

        elif action in ("mine_nearest", "mine"):
            return [command]

        elif action == "place":
            return [command]

        elif action in ("attack", "kill"):
            return [command]

        return [command]

    """
    --------------------------------------------------------------------------------------------
    Function Header - Plan (guided)
    --------------------------------------------------------------------------------------------
    Single shot planning for guided mode. Builds a world state snapshot, formats the user
    prompt with that context, calls the API once, parses the response, resolves any high
    level commands, and returns a flat list of executor-ready commands.

    Guided mode does not accumulate history beyond the current exchange. History is cleared
    before each plan call so each user prompt is treated as a fresh intent in context.
    --------------------------------------------------------------------------------------------
    """
    def plan(self, prompt):
        # cleared every call, each guided prompt is its own intent with no carried context
        self._history = []
        snapshot = self._build_snapshot()
        user_message = (f"World state:\n{json.dumps(snapshot, indent=2)}\n\nInstruction: {prompt}")
        raw = self._call_api(user_message)
        commands = self._parse_commands(raw)

        if not commands:
            commands = [{"action": "chat","message": "I could not determine a valid action for that instruction.",}]

        resolved = []

        # low-level goes straight to the executor, everything else through the resolve seam
        for cmd in commands:
            if cmd.get("action") in self.LOW_LEVEL_ACTIONS:
                resolved.append(cmd)
            else:
                resolved.extend(self._resolve(cmd, snapshot))

        return resolved

    """
    --------------------------------------------------------------------------------------------
    Function Header - Plan loop (autonomous)
    --------------------------------------------------------------------------------------------
    Agentic loop for autonomous mode. Takes a high level goal string and reasons over it
    step by step until the goal is achieved or max_steps is reached.

    Each iteration: builds a fresh snapshot, appends it with the result of the last step
    as context, calls the API, executes the returned commands, then feeds the outcome back
    as the next user message. The model reasons over what it just did and what to do next.

    History is preserved across iterations so the model has full context of the task chain.
    The loop terminates early if the model returns an empty command list, signaling it
    believes the goal is complete.

    The result string passed back each iteration is a plain English summary of what was
    executed, giving the model grounded feedback to reason over for its next decision.

    max_steps is a cost ceiling as much as a safety one, every step is a paid API call, and a
    model that has misread the goal will keep planning plausible steps forever rather than
    returning the empty array that ends the loop.

    The for/else is deliberate. The else runs only when the loop was never broken out of,
    which is exactly the "ran out of steps" case, so completion and exhaustion report
    differently without needing a flag to tell them apart.
    --------------------------------------------------------------------------------------------
    """
    def plan_loop(self, goal, on_step=None, max_steps=20):
        self._history = []
        last_result = "Starting task."

        for step in range(max_steps):
            # checked before the step, after the API call, and after execution, so a stop takes
            # effect at the next boundary instead of after a whole round trip
            if self._stop_event.is_set():
                break

            # drain any mid-task prompts injected by the user and add to history
            while not self._inject_queue.empty():
                injected = self._inject_queue.get_nowait()
                self._history.append({"role": "user", "content": f"Mid-task update: {injected}"})
                self._history.append({"role": "assistant", "content": "Understood, adjusting plan."})
                print(f"Injected prompt applied: {injected}")

            snapshot = self._build_snapshot()

            user_message = (
                f"Goal: {goal}\n\n"
                f"World state:\n{json.dumps(snapshot, indent=2)}\n\n"
                f"Last step result: {last_result}\n\n"
                f"What should the bot do next? If the goal is complete return an empty array []."
            )

            raw = self._call_api(user_message)

            if self._stop_event.is_set():
                break

            commands = self._parse_commands(raw)

            # an empty array is how the model says the goal is done, so it ends the loop
            if not commands:
                print(f"Autonomous loop complete after {step + 1} steps.")
                break

            resolved = []

            for cmd in commands:
                if cmd.get("action") in self.LOW_LEVEL_ACTIONS:
                    resolved.append(cmd)
                else:
                    resolved.extend(self._resolve(cmd, snapshot))

            # on_step runs the batch and reports back, without one this plans but never acts,
            # which is what makes the loop testable without a live connection
            if on_step:
                last_result = on_step(resolved)
            else:
                last_result = f"Planned {len(resolved)} commands without an executor"

            if self._stop_event.is_set():
                break

            print(f"Step {step + 1}: {last_result}")

        # only reached when the loop was never broken out of, so this is the exhausted case
        else:
            print(f"Autonomous loop reached max steps ({max_steps}).")

    # Called from the CLI thread while plan_loop runs on another, so the Queue is doing real
    # work here. The loop drains it between steps rather than mid-step, which is why a new
    # prompt takes effect on the next iteration instead of interrupting the current one.
    def inject(self, prompt):
        # thread-safe injection of a mid-task prompt into the autonomous loop
        # using ordering of prompt queue upon injection
        self._inject_queue.put(prompt)

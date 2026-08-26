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
chat) go directly to the executor. High level intents (find, mine, place) are resolved
by the planner itself using the pathfinder before being handed to the executor as move
commands.

The world state passed to the API is a concise snapshot, not raw chunk objects. Position,
health, food, nearby surface blocks sampled from loaded chunks, and entity positions. This
gives the AI genuine spatial grounding without overwhelming the context window.
--------------------------------------------------------------------------------------------
"""
# imports
import json
import threading
import queue
from command_data import planner_command_error
from model_clients import ModelClientError

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
    LOW_LEVEL_ACTIONS = {"move", "chat"}

    # commands the planner resolves into move sequences before passing to executor
    HIGH_LEVEL_ACTIONS = {"find", "go_to", "mine", "place", "attack"}

    def __init__(self, world_state, model_client=None):
        self._world_state = world_state
        self._model_client = model_client
        # conversation history for autonomous agentic loop
        self._history = []
        # thread-safe queue for mid-task prompt injection in autonomous mode
        self._inject_queue = queue.Queue()

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
        bx = int(pos["x"])
        by = int(pos["y"])
        bz = int(pos["z"])

        nearby = {}
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                wx = bx + dx
                wz = bz + dz
                cx = wx >> 4
                cz = wz >> 4
                chunk = self._world_state["map"].get((cx, cz))
                if chunk is None:
                    continue
                sy = chunk.get_surface_y(wx, wz)
                if sy is not None:
                    block = chunk.get_block(wx, sy, wz)
                    nearby[f"{wx},{sy},{wz}"] = block

        return {
            "position": {"x": bx, "y": by, "z": bz},
            "health": self._world_state["health"],
            "food": self._world_state["food"],
            "inventory": {
                "selected_hotbar_slot": self._world_state.get("inventory", {}).get(
                    "selected_hotbar_slot", 0
                ),
                "slots": {
                    str(slot): {"name": item["name"], "count": item["count"]}
                    for slot, item in self._world_state.get("inventory", {}).get(
                        "slots", {}
                    ).items()
                },
            },
            "nearby_surface_blocks": nearby,
            "entities": {
                str(eid): {
                    "type": e["type"], "name": e.get("name", f"entity_{e['type']}"),
                    "x": int(e["x"]), "y": int(e["y"]), "z": int(e["z"])
                }
                for eid, e in self._world_state["entities"].items()
            }
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
    find:    block (string), radius (int) - resolved by planner into go_to
    mine:    x, y, z (ints) - resolved by planner into go_to + mine action
    place:   x, y, z (ints), block (string) - resolved by planner into go_to + place action
    --------------------------------------------------------------------------------------------
    """
    def _call_api(self, user_message):
        if self._model_client is None:
            print("Planner unavailable: configure a model provider")
            return "[]"

        system = (
            "You are the AI brain of a Minecraft bot with genuine spatial awareness. "
            "You receive a snapshot of the bot's world state and a natural language instruction. "
            "You must respond with ONLY a valid JSON array of command objects and nothing else. "
            "No explanation, no markdown, no preamble. Just the raw JSON array.\n\n"
            "Available actions:\n"
            "  {\"action\": \"move\", \"x\": int, \"y\": int, \"z\": int}\n"
            "  {\"action\": \"chat\", \"message\": string}\n"
            "  {\"action\": \"go_to\", \"x\": int, \"y\": int, \"z\": int}\n"
            "  {\"action\": \"find\", \"block\": string, \"radius\": int}\n"
            "  {\"action\": \"mine\", \"x\": int, \"y\": int, \"z\": int}\n"
            "  {\"action\": \"place\", \"x\": int, \"y\": int, \"z\": int, \"block\": string}\n\n"
            "  {\"action\": \"attack\", \"entity_id\": int}\n\n"
            "Use the world state snapshot to ground your decisions in real coordinates. "
            "Prefer go_to over raw move sequences. Use find when you need to locate a block type. "
            "Keep command lists concise and purposeful."
        )

        self._history.append({
            "role": "user",
            "content": user_message
        })

        try:
            reply = self._model_client.complete(
                system,
                list(self._history),
                self.MAX_TOKENS,
            )
        except ModelClientError as error:
            self._history.pop()
            print(f"Planner model error: {error}")
            return "[]"

        self._history.append({
            "role": "assistant",
            "content": reply
        })

        return reply

    """
    --------------------------------------------------------------------------------------------
    Function Header - Parse commands
    --------------------------------------------------------------------------------------------
    Parses the raw API response string into a list of command dicts. Strips json fences if
    the model includes them despite instructions not to. Returns empty list on parse failure
    rather than crashing so the bot degrades gracefully.
    --------------------------------------------------------------------------------------------
    """
    @staticmethod
    def _parse_commands(raw):
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1]
            clean = clean.rsplit("```", 1)[0]
        try:
            commands = json.loads(clean)
            if not isinstance(commands, list):
                return []
            valid = []
            for command in commands:
                error = planner_command_error(command)
                if error:
                    print(f"Planner command rejected: {error}")
                else:
                    valid.append(command)
            return valid
        except json.JSONDecodeError:
            print(f"Planner parse error: {raw}")
            return []

    """
    --------------------------------------------------------------------------------------------
    Function Header - Resolve high level commands
    --------------------------------------------------------------------------------------------
    High level actions like go_to and find cannot be sent directly to the executor since the
    executor only knows about move and chat packets. This method resolves them into sequences
    of low level move commands using the pathfinder.

    go_to: calls pathfinder.find_path from current position to target, expands into moves
    find:  scans nearby_surface_blocks from the snapshot for the target block type, then
           resolves as go_to once the coordinate is known
    mine remains a high-level command for Bot.mine_block(), which selects a reachable adjacent
    standing position before enqueueing the interaction. Place remains high-level for
    Bot.place_block(), which selects inventory and a valid support face.
    --------------------------------------------------------------------------------------------
    """
    def _resolve(self, command, snapshot):
        action = command.get("action")

        if action == "go_to":
            return [command]

        elif action == "find":
            target_block = command.get("block", "")
            for coord_str, block_name in snapshot["nearby_surface_blocks"].items():
                if block_name == target_block:
                    x, y, z = map(int, coord_str.split(","))
                    return [{"action": "go_to", "x": x, "y": y, "z": z}]
            print(f"Block '{target_block}' not found in loaded chunks")
            return []

        elif action == "mine":
            return [command]

        elif action == "place":
            return [command]

        elif action == "attack":
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
        self._history = []
        snapshot = self._build_snapshot()
        user_message = (
            f"World state:\n{json.dumps(snapshot, indent=2)}\n\n"
            f"Instruction: {prompt}"
        )
        raw = self._call_api(user_message)
        commands = self._parse_commands(raw)
        resolved = []
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
    --------------------------------------------------------------------------------------------
    """
    def plan_loop(self, goal, on_step=None, max_steps=20):
        self._history = []
        last_result = "Starting task."

        for step in range(max_steps):
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
            commands = self._parse_commands(raw)

            if not commands:
                print(f"Autonomous loop complete after {step + 1} steps.")
                break

            resolved = []
            for cmd in commands:
                if cmd.get("action") in self.LOW_LEVEL_ACTIONS:
                    resolved.append(cmd)
                else:
                    resolved.extend(self._resolve(cmd, snapshot))

            if on_step:
                last_result = on_step(resolved)
            else:
                last_result = f"Planned {len(resolved)} commands without an executor"
            print(f"Step {step + 1}: {last_result}")

        else:
            print(f"Autonomous loop reached max steps ({max_steps}).")

    def inject(self, prompt):
        # thread-safe injection of a mid-task prompt into the autonomous loop
        # using ordering of prompt queue upon injection
        self._inject_queue.put(prompt)

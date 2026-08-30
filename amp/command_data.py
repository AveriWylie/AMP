"""
--------------------------------------------------------------------------------------------
Command Data Module - The shared action contract
--------------------------------------------------------------------------------------------
What counts as a valid action, in one place, for the two layers that disagree about where
actions come from. The planner produces commands from model output, the executor consumes
them, and neither should be inventing its own idea of what is legal.

The two tables are deliberately different, and the difference is the point.

PLANNER_COMMAND_FIELDS is what a model is allowed to ask for, with the fields each action
needs and their types, because model output is untrusted and has to be checked before it goes
anywhere near a packet. It carries high-level intents like go_to and mine_nearest, which no
packet corresponds to, since gameplay expands them first.

EXECUTOR_ACTIONS is what can actually be sent, so it holds the low-level primitives the
planner never names directly, swing, sneak, swap_hotbar. It is a set rather than a mapping
because by that point the fields have already been validated here.

The overlap between them is small on purpose. An action in both is one the planner may ask for
and the executor can send unchanged, everything else is translated on the way through.
--------------------------------------------------------------------------------------------
"""

# global constants
PLANNER_COMMAND_FIELDS = {
    "chat": {"message": str},
    "look": {"yaw": (int, float), "pitch": (int, float)},
    "go_to": {"x": int, "y": int, "z": int},
    "mine_nearest": {"block": str, "radius": int},
    "mine": {"x": int, "y": int, "z": int},
    "place": {"x": int, "y": int, "z": int, "block": str},
    "attack": {"entity_id": int},
    "kill": {"entity_id": int},
}

EXECUTOR_ACTIONS = {
    "move", "chat", "look", "swing", "sneak", "attack", "mine", "place",
    "use_item", "select_hotbar", "swap_hotbar",
}


"""
--------------------------------------------------------------------------------------------
Function Header - Command validation
--------------------------------------------------------------------------------------------
Returns why a command is invalid, or None when it is fine. A message rather than a boolean
because the caller prints it, and "invalid fields for place: x, block" tells you what the model
got wrong where a False does not.

Every field is checked before returning rather than stopping at the first bad one, so one pass
names everything wrong with the command instead of revealing it one retry at a time.

The bool check exists because bool subclasses int in Python, so isinstance(True, int) is True
and a model answering true for a coordinate would otherwise validate and be sent as 1. That is
the kind of thing that produces a bot walking to a corner of the world rather than an error.
--------------------------------------------------------------------------------------------
"""
def planner_command_error(command):
    if not isinstance(command, dict):
        return "command must be an object"

    action = command.get("action")
    fields = PLANNER_COMMAND_FIELDS.get(action)

    if fields is None:
        return f"unknown action: {action!r}"

    invalid = []

    for name, expected_type in fields.items():
        value = command.get(name)

        if not isinstance(value, expected_type) or (isinstance(value, bool) and
                                                    (expected_type is int or expected_type == (int, float))):

            invalid.append(name)

    if invalid:
        return f"invalid fields for {action}: {', '.join(invalid)}"

    return None

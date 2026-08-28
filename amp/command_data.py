"""Declarative command contracts shared across planning and execution."""


PLANNER_COMMAND_FIELDS = {
    "chat": {"message": str},
    "look": {"yaw": (int, float), "pitch": (int, float)},
    "go_to": {"x": int, "y": int, "z": int},
    "mine_nearest": {"block": str, "radius": int},
    "mine": {"x": int, "y": int, "z": int},
    "place": {"x": int, "y": int, "z": int, "block": str},
    "attack": {"entity_id": int},
}

EXECUTOR_ACTIONS = {
    "move", "chat", "look", "swing", "sneak", "attack", "mine", "place",
    "use_item", "select_hotbar", "swap_hotbar",
}


def planner_command_error(command):
    """Return a validation error for model-produced commands, or None when valid."""
    if not isinstance(command, dict):
        return "command must be an object"
    action = command.get("action")
    fields = PLANNER_COMMAND_FIELDS.get(action)
    if fields is None:
        return f"unknown action: {action!r}"
    invalid = []
    for name, expected_type in fields.items():
        value = command.get(name)
        if not isinstance(value, expected_type) or (
            isinstance(value, bool)
            and (expected_type is int or expected_type == (int, float))
        ):
            invalid.append(name)
    if invalid:
        return f"invalid fields for {action}: {', '.join(invalid)}"
    return None

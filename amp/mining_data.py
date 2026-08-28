"""Tool selection and base vanilla survival mining durations."""

import json
import math
from functools import lru_cache
from pathlib import Path


TOOL_SPEEDS = {
    "wooden": 2.0,
    "stone": 4.0,
    "iron": 6.0,
    "diamond": 8.0,
    "netherite": 9.0,
    "golden": 12.0,
}


@lru_cache(maxsize=None)
def block_data(version):
    path = Path(__file__).parent / "blocks" / f"blocks_{version}.json"
    return {
        block["name"]: block
        for block in json.loads(path.read_text(encoding="utf-8"))
    }


def _tool_details(item, material):
    tool_type = material.removeprefix("mineable/") if material else None
    name = item["name"]
    if not tool_type or not name.endswith(f"_{tool_type}"):
        return 1.0
    tier = name.removesuffix(f"_{tool_type}")
    return TOOL_SPEEDS.get(tier, 1.0)


def mining_plan(version, block_name, inventory):
    """Return inventory/tool selection and base break time, or None if unsafe."""
    block = block_data(version).get(block_name)
    if not block or not block.get("diggable", False) or block.get("hardness", -1) < 0:
        return None

    harvest_tools = block.get("harvestTools")
    candidates = [(True, 1.0, None, None)] if not harvest_tools else []
    for inventory_slot in range(9, 45):
        item = inventory["slots"].get(inventory_slot)
        if item is None:
            continue
        can_harvest = not harvest_tools or str(item["id"]) in harvest_tools
        speed = _tool_details(item, block.get("material"))
        candidates.append((can_harvest, speed, inventory_slot, item))

    if harvest_tools:
        candidates = [candidate for candidate in candidates if candidate[0]]
        if not candidates:
            return None

    if candidates:
        can_harvest, speed, inventory_slot, item = max(
            candidates, key=lambda candidate: (candidate[0], candidate[1])
        )
    else:
        return None

    divisor = 30.0 if can_harvest else 100.0
    damage_per_tick = speed / block["hardness"] / divisor
    ticks = max(1, math.ceil(1.0 / damage_per_tick))
    return {
        "inventory_slot": inventory_slot,
        "hotbar_slot": (
            inventory_slot - 36 if inventory_slot is not None and inventory_slot >= 36
            else inventory.get("selected_hotbar_slot", 0)
        ),
        "item": item,
        "ticks": ticks,
        "seconds": ticks / 20.0,
    }

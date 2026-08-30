"""Tool selection and base vanilla survival mining durations."""

# imports
import json
import math
from functools import lru_cache
from pathlib import Path


# global constants
# Vanilla tool tier multipliers. Gold is fastest and worst, it mines quicker than diamond but
# has almost no durability, which is why the number looks out of order next to the others.
TOOL_SPEEDS = {
    "wooden": 2.0,
    "stone": 4.0,
    "iron": 6.0,
    "diamond": 8.0,
    "netherite": 9.0,
    "golden": 12.0,
}


# Same registry chunk.py reads, keyed by name instead of state ID because mining asks "how hard
# is stone" rather than "what is state 1". Cached per version, the file never changes at runtime.
@lru_cache(maxsize=None)
def block_data(version):
    path = Path(__file__).parent / "blocks" / f"blocks_{version}.json"

    return {block["name"]: block for block in json.loads(path.read_text(encoding="utf-8"))}


"""
--------------------------------------------------------------------------------------------
Function Header - Tool speed
--------------------------------------------------------------------------------------------
Works out how fast one held item mines one block, by name. The registry stores a block's
material as something like "mineable/pickaxe", and vanilla item names end in the matching tool
type, so "diamond_pickaxe" against "mineable/pickaxe" splits into tier "diamond".

Returns 1.0, bare hand speed, whenever the pairing does not hold. That covers a block with no
tool material, an item that is not the right tool, and a tool tier the table does not know
about. All three mean the same thing to the caller, no speed bonus, so they share one answer
rather than being distinguished for no purpose.
--------------------------------------------------------------------------------------------
"""
def _tool_details(item, material):
    tool_type = material.removeprefix("mineable/") if material else None
    name = item["name"]

    if not tool_type or not name.endswith(f"_{tool_type}"):
        return 1.0

    tier = name.removesuffix(f"_{tool_type}")
    return TOOL_SPEEDS.get(tier, 1.0)


"""
--------------------------------------------------------------------------------------------
Function Header - Mining plan
--------------------------------------------------------------------------------------------
Picks the best tool in the inventory for one block and works out how long the break takes.
Returns None rather than a plan whenever mining is not worth attempting, so the caller has one
thing to check instead of a plan it has to second-guess.

There are two distinct None cases. The block itself may be unmineable, bedrock and air have
negative or absent hardness, and no tool changes that. Or the block may require a specific
harvest tool that is not in the inventory, which means you could swing at it forever and never
get a drop.

An absent or empty harvestTools both mean anything harvests the block, so both seed a
bare-hand candidate up front. Empty is treated the same as absent deliberately, some registry
entries carry an empty list where they mean "no tool required", and reading that as "no tool
can harvest this" would refuse to mine blocks that break fine by hand.

Slots 9 to 44 are the main inventory plus the hotbar, skipping armour and crafting slots, which
cannot hold a tool worth selecting.

The scoring sorts on harvest ability first and speed second, deliberately. A slow tool that
yields a drop beats a fast one that yields nothing.

The divisor is vanilla's constant, 30 when the tool harvests and 100 when it does not, which is
what makes wrong-tool mining roughly three times slower on top of any speed difference.
--------------------------------------------------------------------------------------------
"""
def mining_plan(version, block_name, inventory):
    block = block_data(version).get(block_name)

    # unmineable by nature, no tool in the game changes this
    if not block or not block.get("diggable", False) or block.get("hardness", -1) < 0:
        return None

    harvest_tools = block.get("harvestTools")
    # absent or empty both mean bare hands already work, so seed that as a candidate
    candidates = [(True, 1.0, None, None)] if not harvest_tools else []

    # 9 to 44 is main inventory plus hotbar, armour and crafting slots hold nothing useful here
    for inventory_slot in range(9, 45):
        item = inventory["slots"].get(inventory_slot)

        if item is None:
            continue

        can_harvest = not harvest_tools or str(item["id"]) in harvest_tools
        speed = _tool_details(item, block.get("material"))
        candidates.append((can_harvest, speed, inventory_slot, item))

    # the block demands a specific tool, so anything that cannot harvest is not a candidate
    if harvest_tools:
        candidates = [candidate for candidate in candidates if candidate[0]]
        if not candidates:
            return None

    # harvest ability outranks speed, a slow tool that drops the block beats a fast one that does not
    if candidates:
        can_harvest, speed, inventory_slot, item = max(candidates, key=lambda candidate: (candidate[0], candidate[1]))
    else:
        return None

    # vanilla constants, wrong-tool mining is penalised on top of any speed difference
    divisor = 30.0 if can_harvest else 100.0
    damage_per_tick = speed / block["hardness"] / divisor
    # at least one tick, an instant-break block still costs a swing
    ticks = max(1, math.ceil(1.0 / damage_per_tick))

    return {
        "inventory_slot": inventory_slot,

        # hotbar slots are 36 to 44, so subtract the offset, otherwise keep whatever is selected
        "hotbar_slot": (
            inventory_slot - 36 if inventory_slot is not None and inventory_slot >= 36
            else inventory.get("selected_hotbar_slot", 0)
        ),

        "item": item,
        "ticks": ticks,
        # 20 ticks per second, given back so callers do not have to know the tick rate
        "seconds": ticks / 20.0,
    }

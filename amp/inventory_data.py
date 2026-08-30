"""Load the checked-in item registry used to name inventory slot contents."""

# imports
import json
from functools import lru_cache
from pathlib import Path


"""
--------------------------------------------------------------------------------------------
Function Header - Item name registry
--------------------------------------------------------------------------------------------
Loads the generated item registry for one Minecraft version and maps numeric item IDs to
their names. Inventory packets carry IDs, everything above the protocol layer wants names, so
this sits between them the same way entity_data does for mobs.

Cached on version for the same reason, the file never changes at runtime and inventory
updates arrive on every slot change, so re-parsing the JSON per update would be wasteful.

Catches FileNotFoundError rather than checking exists() first. Both work, but the try avoids
the window between the check and the read, and it is the same outcome either way, an empty
map so a version without generated item data still runs with numeric fallback names.
--------------------------------------------------------------------------------------------
"""
@lru_cache(maxsize=None)
def item_names(version):
    path = Path(__file__).parent / "items" / f"items_{version}.json"

    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}

    return {item["id"]: item["name"] for item in items}


# Public lookup. Unknown IDs are prefixed rather than dropped so the caller can still tell two
# unrecognized items apart, and so a missing registry entry never silently reads as "no item".
def item_name(version, item_id):
    return item_names(version).get(item_id, f"unknown:{item_id}")

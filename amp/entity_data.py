"""Load entity names from the pinned minecraft-data registry."""

# imports
import json
from functools import lru_cache
from pathlib import Path


"""
--------------------------------------------------------------------------------------------
Function Header - Entity name registry
--------------------------------------------------------------------------------------------
Loads the generated entity registry for one Minecraft version and maps numeric entity type
IDs to their names. The IDs are what the server puts in spawn packets, the names are what the
planner and the rest of AMP actually reason about, so this is the translation layer between
the two.

Cached on version because the file is read once per version and never changes at runtime, and
because entity spawns arrive constantly. Every mob that walks into render distance would
otherwise re-read and re-parse the whole JSON file.

A missing file returns an empty map rather than raising. A version with no generated entity
data still connects and plays, you just get numeric fallback names out of entity_name, which
is a degraded lookup and not a broken bot.
--------------------------------------------------------------------------------------------
"""
@lru_cache(maxsize=None)
def _entity_names(version):
    path = Path(__file__).parent / "entities" / f"entities_{version}.json"

    if not path.exists():
        return {}

    entities = json.loads(path.read_text(encoding="utf-8"))
    return {entity["id"]: entity["name"] for entity in entities}


# Public lookup. Unknown IDs keep their number rather than collapsing to one shared placeholder,
# so two different unrecognized mobs stay distinguishable in logs and planner context.
def entity_name(version, entity_id):
    return _entity_names(version).get(entity_id, f"entity_{entity_id}")

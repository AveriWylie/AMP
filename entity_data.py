"""Load entity names from the pinned minecraft-data registry."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def _entity_names(version):
    path = Path(__file__).parent / "entities" / f"entities_{version}.json"
    if not path.exists():
        return {}
    entities = json.loads(path.read_text(encoding="utf-8"))
    return {entity["id"]: entity["name"] for entity in entities}


def entity_name(version, entity_id):
    return _entity_names(version).get(entity_id, f"entity_{entity_id}")

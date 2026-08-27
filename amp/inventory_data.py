"""Load the checked-in item registry used to name inventory slot contents."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def item_names(version):
    path = Path(__file__).parent / "items" / f"items_{version}.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {item["id"]: item["name"] for item in items}


def item_name(version, item_id):
    """Return a stable readable name, retaining unknown IDs without data loss."""
    return item_names(version).get(item_id, f"unknown:{item_id}")

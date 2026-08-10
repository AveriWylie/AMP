"""Load the compact protocol tables generated from PrismarineJS minecraft-data."""

import json
from functools import lru_cache
from pathlib import Path


PROTOCOL_TABLE_PATH = Path(__file__).parent / "protocol" / "packet_ids.json"


@lru_cache(maxsize=1)
def load_protocol_tables():
    """Return the checked-in protocol table, validating its public shape."""
    data = json.loads(PROTOCOL_TABLE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data.get("versions"), dict) or not data["versions"]:
        raise ValueError("Protocol table has no versions")
    return data


def version_protocols():
    """Map supported Minecraft version strings to protocol numbers."""
    return {
        version: entry["protocol"]
        for version, entry in load_protocol_tables()["versions"].items()
    }


def packet_ids(version, direction):
    """Return a copy of the named packet IDs for one version and direction."""
    if direction not in ("clientbound", "serverbound"):
        raise ValueError(f"Unknown packet direction: {direction}")

    versions = load_protocol_tables()["versions"]
    try:
        return dict(versions[version][direction])
    except KeyError as exc:
        raise ValueError(f"No generated protocol data for Minecraft {version}") from exc


def packet_ids_for_protocol(protocol, direction):
    """Return packet IDs for a numeric protocol, allowing version aliases."""
    if direction not in ("clientbound", "serverbound"):
        raise ValueError(f"Unknown packet direction: {direction}")

    for entry in load_protocol_tables()["versions"].values():
        if entry["protocol"] == protocol:
            return dict(entry[direction])
    raise ValueError(f"No generated packet data for protocol {protocol}")

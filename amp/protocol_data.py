
# imports
import json
from functools import lru_cache
from pathlib import Path


# global constants
PROTOCOL_TABLE_PATH = Path(__file__).parent / "protocol" / "packet_ids.json"


"""
--------------------------------------------------------------------------------------------
Function Header - Protocol table loader
--------------------------------------------------------------------------------------------
Reads the generated packet ID table once and keeps it. Cached with maxsize=1 rather than on a
version key because there is only ever one table, every version lives inside it, so a single
slot is the whole cache.

The validation is deliberately shallow, it only asserts that a non-empty versions map exists.
Deeper checking belongs to the callers below, which each know exactly which slice they need
and can say something useful when it is missing. Checking every version here would mean this
function failing over data no caller was going to touch.
--------------------------------------------------------------------------------------------
"""
@lru_cache(maxsize=1)
def load_protocol_tables():
    data = json.loads(PROTOCOL_TABLE_PATH.read_text(encoding="utf-8"))

    if not isinstance(data.get("versions"), dict) or not data["versions"]:
        raise ValueError("Protocol table has no versions")

    return data


# Maps supported Minecraft version strings to their protocol numbers, the whole table flattened
# to the one field callers outside this module actually reason about.
def version_protocols():
    return {version: entry["protocol"] for version, entry in load_protocol_tables()["versions"].items()}


"""
--------------------------------------------------------------------------------------------
Function Field Header - Packet ID lookups
--------------------------------------------------------------------------------------------
Two ways into the same table, by version string and by protocol number. Both exist because the
two identifiers are not interchangeable. A version string is what a human types, a protocol
number is what the handshake sends, and several versions can share one protocol number, which
is exactly why the numeric lookup scans for a match instead of indexing directly.

Both return a copy rather than the cached dict. The table is loaded once and shared, so
handing out the live object would let one caller mutate the packet IDs every other caller
reads, and that corruption would surface far away from whatever caused it.

play is the default and is stored flat at the top of each entry, since it carries almost every
packet. login and configuration are nested under states because they are small and only matter
during the handshake, which is the shape the table generator writes.
--------------------------------------------------------------------------------------------
"""
def packet_ids(version, direction, state="play"):
    if direction not in ("clientbound", "serverbound"):
        raise ValueError(f"Unknown packet direction: {direction}")

    versions = load_protocol_tables()["versions"]

    # one try over the whole chain, any missing link is the same answer to the caller
    try:
        entry = versions[version]
        table = entry if state == "play" else entry["states"][state]
        return dict(table[direction])
    except KeyError as exc:
        raise ValueError(f"No generated {state} protocol data for Minecraft {version}") from exc


def packet_ids_for_protocol(protocol, direction, state="play"):

    if direction not in ("clientbound", "serverbound"):
        raise ValueError(f"Unknown packet direction: {direction}")

    # scan rather than index, several version strings can share one protocol number
    for entry in load_protocol_tables()["versions"].values():
        if entry["protocol"] == protocol:
            table = entry if state == "play" else entry.get("states", {}).get(state)
            # first match settles it, a later alias would carry the same data
            if table is None or direction not in table:
                break

            return dict(table[direction])

    raise ValueError(f"No generated {state} packet data for protocol {protocol}")

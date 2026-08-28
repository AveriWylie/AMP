"""Generate AMP's compact packet table from pinned minecraft-data definitions."""

import argparse
import json
import sys
import urllib.request
from pathlib import Path


REPOSITORY = "https://github.com/PrismarineJS/minecraft-data"
REVISION = "105097328f99a4f45cb6dca0fbef97db0cbd1cfd"
RAW_ROOT = f"https://raw.githubusercontent.com/PrismarineJS/minecraft-data/{REVISION}/data/pc"
DATA_ROOT = Path(__file__).resolve().parents[1] / "amp"
OUTPUT = DATA_ROOT / "protocol" / "packet_ids.json"
BLOCK_OUTPUTS = {
    version: DATA_ROOT / "blocks" / f"blocks_{version}.json"
    for version in ("26.1", "26.1.1", "26.1.2", "26.2")
}
ITEM_OUTPUTS = {
    version: DATA_ROOT / "items" / f"items_{version}.json"
    for version in ("26.1", "26.1.1", "26.1.2", "26.2")
}
ENTITY_OUTPUTS = {
    version: DATA_ROOT / "entities" / f"entities_{version}.json"
    for version in ("26.1", "26.1.1", "26.1.2", "26.2")
}

VERSION_SOURCES = {
    "26.1": "26.1",
    "26.1.1": "26.1",
    "26.1.2": "26.1",
    # The pinned upstream index declares protocol 776 but has no 26.2 directory.
    # Packet IDs remain compatible with 26.1; official server reports supply the
    # 26.2 registries through tools/import_server_reports.py.
    "26.2": "26.1",
}

OFFICIAL_REPORT_REGISTRIES = {"26.2"}

PLAY_PACKETS = {
    "clientbound": (
        "spawn_entity",
        "login",
        "rel_entity_move",
        "entity_move_look",
        "entity_destroy",
        "entity_teleport",
        "block_change",
        "multi_block_change",
        "keep_alive",
        "map_chunk",
        "position",
        "respawn",
        "update_health",
        "window_items",
        "set_slot",
        "held_item_slot",
    ),
    "serverbound": (
        "teleport_confirm",
        "chat_message",
        "client_command",
        "keep_alive",
        "position",
        "look",
        "block_dig",
        "entity_action",
        "arm_animation",
        "block_place",
        "use_item",
        "held_item_slot",
        "window_click",
        "use_entity",
    ),
}

JAVA_26_PLAY_PACKETS = {
    "clientbound": ("start_configuration",
        "sync_entity_position", "set_player_inventory", "set_cursor_item",
    ),
    "serverbound": ("configuration_acknowledged", "player_input", "attack"),
}

JAVA_26_CONFIGURATION_PACKETS = {
    "clientbound": (
        "add_resource_pack", "clear_dialog", "code_of_conduct", "cookie_request",
        "custom_payload", "custom_report_details", "disconnect", "feature_flags",
        "finish_configuration", "keep_alive", "ping", "registry_data",
        "remove_resource_pack", "reset_chat", "select_known_packs", "server_links",
        "show_dialog", "store_cookie", "tags", "transfer",
    ),
    "serverbound": (
        "accept_code_of_conduct", "cookie_response", "custom_click_action",
        "custom_payload", "finish_configuration", "keep_alive", "pong",
        "resource_pack_receive", "select_known_packs", "settings",
    ),
}

LOGIN_PACKETS = {
    "clientbound": (
        "compress", "disconnect", "encryption_begin", "login_plugin_request", "success",
    ),
    "serverbound": (
        "encryption_begin", "login_acknowledged", "login_plugin_response", "login_start",
    ),
}

JAVA_26_LOGIN_PACKETS = {
    "clientbound": LOGIN_PACKETS["clientbound"] + ("cookie_request",),
    "serverbound": LOGIN_PACKETS["serverbound"] + ("cookie_response",),
}


def fetch_json(relative_path):
    request = urllib.request.Request(
        f"{RAW_ROOT}/{relative_path}",
        headers={"User-Agent": "AMP-protocol-table-generator"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def find_packet_mapping(protocol, direction, required_names, state="play"):
    """Extract the packet-name mapper from a minecraft-data protocol definition."""
    root = protocol[state]["toClient" if direction == "clientbound" else "toServer"]
    candidates = []

    def visit(value):
        if isinstance(value, dict):
            mappings = value.get("mappings")
            if isinstance(mappings, dict):
                candidates.append(mappings)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(root["types"]["packet"])
    required = set(required_names)
    for mapping in candidates:
        if required.issubset(mapping.values()):
            return {name: int(packet_id, 0) for packet_id, name in mapping.items()}
    raise ValueError(f"Could not find {direction} packet mapping containing {sorted(required)}")


def build_table(fetch=fetch_json):
    protocol_versions = fetch("common/protocolVersions.json")
    version_numbers = {
        entry["minecraftVersion"]: entry["version"]
        for entry in protocol_versions
    }
    definitions = {}
    versions = {}

    for version, source_version in VERSION_SOURCES.items():
        if source_version not in definitions:
            definitions[source_version] = fetch(f"{source_version}/protocol.json")
        protocol = definitions[source_version]
        entry = {
            "protocol": version_numbers[version],
            "source_version": source_version,
        }
        play_packets = {
            direction: names + JAVA_26_PLAY_PACKETS[direction]
            for direction, names in PLAY_PACKETS.items()
        }
        for direction, names in play_packets.items():
            mapping = find_packet_mapping(protocol, direction, names, state="play")
            entry[direction] = {name: mapping[name] for name in names}
        if version in VERSION_SOURCES:
            entry["states"] = {}
            for state, packets in (
                ("login", JAVA_26_LOGIN_PACKETS),
                ("configuration", JAVA_26_CONFIGURATION_PACKETS),
            ):
                entry["states"][state] = {}
                for direction, names in packets.items():
                    mapping = find_packet_mapping(protocol, direction, names, state=state)
                    entry["states"][state][direction] = {
                        name: mapping[name] for name in names
                    }
        versions[version] = entry

    return {
        "source": {
            "repository": REPOSITORY,
            "revision": REVISION,
            "license": "MIT",
        },
        "versions": versions,
    }


def render_table(table):
    return json.dumps(table, indent=2, sort_keys=True) + "\n"


def render_blocks(blocks):
    """Render upstream block registries compactly and deterministically."""
    return json.dumps(blocks, separators=(",", ":"), sort_keys=True) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the checked-in table differs from freshly generated output",
    )
    args = parser.parse_args(argv)
    rendered = render_table(build_table())
    rendered_blocks = {
        version: render_blocks(fetch_json(f"{VERSION_SOURCES[version]}/blocks.json"))
        for version in BLOCK_OUTPUTS if version not in OFFICIAL_REPORT_REGISTRIES
    }
    rendered_items = {
        version: render_blocks(fetch_json(f"{VERSION_SOURCES[version]}/items.json"))
        for version in ITEM_OUTPUTS if version not in OFFICIAL_REPORT_REGISTRIES
    }
    rendered_entities = {
        version: render_blocks(fetch_json(f"{VERSION_SOURCES[version]}/entities.json"))
        for version in ENTITY_OUTPUTS if version not in OFFICIAL_REPORT_REGISTRIES
    }

    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            print(f"Generated protocol data is stale: {OUTPUT}", file=sys.stderr)
            return 1
        for version, path in BLOCK_OUTPUTS.items():
            if version in OFFICIAL_REPORT_REGISTRIES:
                continue
            if not path.exists() or path.read_text(encoding="utf-8") != rendered_blocks[version]:
                print(f"Generated block data is stale: {path}", file=sys.stderr)
                return 1
        for version, path in ITEM_OUTPUTS.items():
            if version in OFFICIAL_REPORT_REGISTRIES:
                continue
            if not path.exists() or path.read_text(encoding="utf-8") != rendered_items[version]:
                print(f"Generated item data is stale: {path}", file=sys.stderr)
                return 1
        for version, path in ENTITY_OUTPUTS.items():
            if version in OFFICIAL_REPORT_REGISTRIES:
                continue
            if not path.exists() or path.read_text(encoding="utf-8") != rendered_entities[version]:
                print(f"Generated entity data is stale: {path}", file=sys.stderr)
                return 1
        print(f"Protocol data is current: {OUTPUT}")
        print("Block data is current: " + ", ".join(map(str, BLOCK_OUTPUTS.values())))
        print("Item data is current: " + ", ".join(map(str, ITEM_OUTPUTS.values())))
        print("Entity data is current: " + ", ".join(map(str, ENTITY_OUTPUTS.values())))
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    temporary.replace(OUTPUT)
    print(f"Wrote {OUTPUT}")
    for version, path in BLOCK_OUTPUTS.items():
        if version in OFFICIAL_REPORT_REGISTRIES:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(rendered_blocks[version], encoding="utf-8", newline="\n")
        temporary.replace(path)
        print(f"Wrote {path}")
    for version, path in ITEM_OUTPUTS.items():
        if version in OFFICIAL_REPORT_REGISTRIES:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(rendered_items[version], encoding="utf-8", newline="\n")
        temporary.replace(path)
        print(f"Wrote {path}")
    for version, path in ENTITY_OUTPUTS.items():
        if version in OFFICIAL_REPORT_REGISTRIES:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(rendered_entities[version], encoding="utf-8", newline="\n")
        temporary.replace(path)
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

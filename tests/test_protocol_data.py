"""Tests for generated minecraft-data packet tables and extraction helpers."""

import pytest

from amp.protocol_data import (
    load_protocol_tables,
    packet_ids,
    packet_ids_for_protocol,
    version_protocols,
)
from tools.sync_minecraft_data import find_packet_mapping


def test_generated_table_records_pinned_source():
    source = load_protocol_tables()["source"]
    assert source == {
        "license": "MIT",
        "repository": "https://github.com/PrismarineJS/minecraft-data",
        "revision": "105097328f99a4f45cb6dca0fbef97db0cbd1cfd",
    }


def test_generated_versions_and_aliases():
    assert version_protocols() == {
        "26.1": 775,
        "26.1.1": 775,
        "26.1.2": 775,
        "26.2": 776,
    }
    assert packet_ids("26.1", "clientbound") == packet_ids("26.1.2", "clientbound")
    assert packet_ids_for_protocol(775, "serverbound") == packet_ids(
        "26.1", "serverbound"
    )


def test_packet_table_results_are_copies():
    ids = packet_ids("26.1", "clientbound")
    ids["position"] = -1
    assert packet_ids("26.1", "clientbound")["position"] != -1


@pytest.mark.parametrize("direction", ["sideways", ""])
def test_packet_table_rejects_unknown_direction(direction):
    with pytest.raises(ValueError):
        packet_ids("26.1", direction)


def test_packet_table_rejects_unknown_version_and_protocol():
    with pytest.raises(ValueError):
        packet_ids("9.9.9", "clientbound")
    with pytest.raises(ValueError):
        packet_ids_for_protocol(9999, "serverbound")


def test_generator_finds_packet_mapper_without_layout_offsets():
    protocol = {
        "play": {
            "toClient": {
                "types": {
                    "packet": [
                        "container",
                        {"nested": {"mappings": {"0x01": "spawn", "0x80": "large"}}},
                    ]
                }
            }
        }
    }
    assert find_packet_mapping(protocol, "clientbound", ("spawn", "large")) == {
        "spawn": 1,
        "large": 128,
    }

"""Tests for generated minecraft-data packet tables and extraction helpers."""

import pytest

from protocol_data import (
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
        "revision": "e8ff8ec779a48814c2fc5b8a0ba7c95b9bc05d6d",
    }


def test_generated_versions_and_aliases():
    assert version_protocols() == {
        "1.19.4": 762,
        "1.20": 763,
        "1.20.1": 763,
        "1.20.2": 764,
    }
    assert packet_ids("1.20", "clientbound") == packet_ids("1.20.1", "clientbound")
    assert packet_ids_for_protocol(763, "serverbound") == packet_ids(
        "1.20", "serverbound"
    )


def test_1202_configuration_packet_tables():
    assert packet_ids("1.20.2", "clientbound", state="configuration")[
        "finish_configuration"
    ] == 0x02
    assert packet_ids_for_protocol(764, "serverbound", state="login")[
        "login_acknowledged"
    ] == 0x03
    assert packet_ids_for_protocol(764, "serverbound", state="configuration")[
        "finish_configuration"
    ] == 0x02


def test_legacy_protocol_has_no_configuration_state():
    with pytest.raises(ValueError):
        packet_ids_for_protocol(763, "serverbound", state="configuration")


def test_packet_table_results_are_copies():
    ids = packet_ids("1.19.4", "clientbound")
    ids["position"] = -1
    assert packet_ids("1.19.4", "clientbound")["position"] == 0x3C


@pytest.mark.parametrize("direction", ["sideways", ""])
def test_packet_table_rejects_unknown_direction(direction):
    with pytest.raises(ValueError):
        packet_ids("1.19.4", direction)


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

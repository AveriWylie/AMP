import json
from pathlib import Path

from amp.command_data import EXECUTOR_ACTIONS
from amp.protocol_data import packet_ids
from amp.version_support import load_support_manifest


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "protocol"
REQUIRED_EVENTS = {
    "login_success", "finish_configuration", "position", "health", "chunk",
    "block", "entity_spawn", "entity_move", "entity_remove", "inventory", "slot",
}


def _fixtures():
    return [json.loads(path.read_text(encoding="utf-8")) for path in FIXTURE_ROOT.glob("*.json")]


def test_every_manifest_family_has_a_complete_fixture_contract():
    manifest = load_support_manifest()
    fixtures = {fixture["family"]: fixture for fixture in _fixtures()}

    assert set(fixtures) == {entry["family"] for entry in manifest["versions"].values()}
    for family, fixture in fixtures.items():
        expected_versions = {
            version for version, entry in manifest["versions"].items()
            if entry["family"] == family
        }
        assert set(fixture["versions"]) == expected_versions
        assert set(fixture["clientbound"]) == REQUIRED_EVENTS
        assert set(fixture["actions"]) == EXECUTOR_ACTIONS


def test_fixture_packet_names_exist_for_every_family_version():
    for fixture in _fixtures():
        for version in fixture["versions"]:
            for packet in fixture["clientbound"].values():
                assert packet["name"] in packet_ids(
                    version, "clientbound", state=packet["state"]
                )
            serverbound = packet_ids(version, "serverbound")
            assert set(fixture["actions"].values()) <= set(serverbound)

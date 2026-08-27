import json

from tools.run_local_world import (
    add_operator,
    offline_player_uuid,
    update_properties,
)


def test_update_properties_preserves_unmanaged_settings(tmp_path):
    path = tmp_path / "server.properties"
    path.write_text("difficulty=hard\nserver-port=25565\n", encoding="utf-8")

    update_properties(path, {"server-port": 25576, "online-mode": "false"})

    assert path.read_text(encoding="utf-8") == (
        "difficulty=hard\nserver-port=25576\nonline-mode=false\n"
    )


def test_add_operator_uses_offline_uuid_and_is_idempotent(tmp_path):
    path = tmp_path / "ops.json"

    add_operator(path, "Notch")
    add_operator(path, "Notch")

    assert offline_player_uuid("Notch") == "b50ad385-829d-3141-a216-7e7d7539ba7f"
    assert json.loads(path.read_text(encoding="utf-8")) == [{
        "uuid": "b50ad385-829d-3141-a216-7e7d7539ba7f",
        "name": "Notch",
        "level": 4,
        "bypassesPlayerLimit": False,
    }]

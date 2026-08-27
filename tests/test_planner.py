from amp.model_clients import ModelClientError
from amp.planner import Planner


class FakeModelClient:
    def __init__(self, reply="[]", error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def complete(self, system, messages, max_tokens):
        self.calls.append((system, messages, max_tokens))
        if self.error:
            raise self.error
        return self.reply


def test_mine_resolution_preserves_interaction_for_bot():
    planner = Planner({})
    command = {"action": "mine", "x": 1, "y": 64, "z": 2}

    assert planner._resolve(command, {}) == [command]


def test_place_resolution_preserves_interaction_for_bot():
    planner = Planner({})
    command = {"action": "place", "x": 1, "y": 64, "z": 2, "block": "oak_planks"}
    assert planner._resolve(command, {}) == [command]


def test_attack_resolution_preserves_tracked_entity_id():
    planner = Planner({})
    command = {"action": "attack", "entity_id": 42}
    assert planner._resolve(command, {}) == [command]


def test_call_api_uses_model_client_and_records_history():
    client = FakeModelClient('[{"action":"chat","message":"hi"}]')
    planner = Planner({}, model_client=client)

    reply = planner._call_api("Say hi")

    assert reply == '[{"action":"chat","message":"hi"}]'
    system, messages, max_tokens = client.calls[0]
    assert max_tokens == Planner.MAX_TOKENS
    assert messages == [
        {"role": "user", "content": "Say hi"},
    ]
    assert "Minecraft bot" in system


def test_call_api_without_credentials_degrades_gracefully(capsys):
    planner = Planner({})

    assert planner._call_api("Say hi") == "[]"
    assert planner._history == []
    assert "Planner unavailable" in capsys.readouterr().out


def test_call_api_handles_model_errors_without_poisoning_history(capsys):
    client = FakeModelClient(error=ModelClientError("provider unavailable"))
    planner = Planner({}, model_client=client)
    planner._history = [{"role": "user", "content": "Earlier context"}]

    assert planner._call_api("Try this") == "[]"
    assert planner._history == [{"role": "user", "content": "Earlier context"}]
    assert "provider unavailable" in capsys.readouterr().out


def test_parse_commands_rejects_unknown_or_malformed_actions(capsys):
    raw = """[
        {"action": "chat", "message": "hello"},
        {"action": "dance"},
        {"action": "move", "x": 1, "y": 64},
        {"action": "attack", "entity_id": true}
    ]"""

    assert Planner._parse_commands(raw) == [
        {"action": "chat", "message": "hello"}
    ]
    output = capsys.readouterr().out
    assert "unknown action" in output
    assert "invalid fields" in output

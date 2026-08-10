from types import SimpleNamespace

import httpx
from anthropic import APIConnectionError

from planner import Planner


class FakeMessages:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.content)


class FakeAnthropic:
    def __init__(self, content):
        self.messages = FakeMessages(content)


def test_call_api_uses_sdk_and_records_history():
    client = FakeAnthropic([
        SimpleNamespace(type="text", text='[{"action":"chat","message":"hi"}]')
    ])
    planner = Planner({}, client=client)

    reply = planner._call_api("Say hi")

    assert reply == '[{"action":"chat","message":"hi"}]'
    assert client.messages.calls[0]["model"] == Planner.MODEL
    assert client.messages.calls[0]["max_tokens"] == Planner.MAX_TOKENS
    assert client.messages.calls[0]["messages"] == [
        {"role": "user", "content": "Say hi"},
    ]
    assert "Minecraft bot" in client.messages.calls[0]["system"]


def test_call_api_combines_only_text_content_blocks():
    client = FakeAnthropic([
        SimpleNamespace(type="text", text="["),
        SimpleNamespace(type="tool_use", name="ignored"),
        SimpleNamespace(type="text", text="]"),
    ])
    planner = Planner({}, client=client)

    assert planner._call_api("Done?") == "[]"


def test_call_api_without_credentials_degrades_gracefully(capsys):
    planner = Planner({})

    assert planner._call_api("Say hi") == "[]"
    assert planner._history == []
    assert "Planner unavailable" in capsys.readouterr().out


def test_call_api_uses_configured_model():
    client = FakeAnthropic([SimpleNamespace(type="text", text="[]")])
    planner = Planner({}, client=client, model="claude-test-model")

    planner._call_api("Done?")

    assert client.messages.calls[0]["model"] == "claude-test-model"


def test_call_api_handles_sdk_errors_without_poisoning_history(capsys):
    class FailingMessages:
        @staticmethod
        def create(**kwargs):
            request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            raise APIConnectionError(request=request)

    client = SimpleNamespace(messages=FailingMessages())
    planner = Planner({}, client=client)
    planner._history = [{"role": "user", "content": "Earlier context"}]

    assert planner._call_api("Try this") == "[]"
    assert planner._history == [{"role": "user", "content": "Earlier context"}]
    assert "APIConnectionError" in capsys.readouterr().out

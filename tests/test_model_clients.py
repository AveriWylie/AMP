"""Provider adapter contract tests."""

import json
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from amp import model_clients
from amp.model_clients import (
    AnthropicModelClient,
    ModelClientError,
    OpenAICompatibleModelClient,
    build_model_client,
)


class FakeAnthropicMessages:
    def __init__(self, content=None, error=None):
        self.content = content or []
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.content)


def test_anthropic_adapter_normalizes_text_and_request():
    messages_api = FakeAnthropicMessages([
        SimpleNamespace(type="text", text="["),
        SimpleNamespace(type="tool_use", text="ignored"),
        SimpleNamespace(type="text", text="]"),
    ])
    sdk = SimpleNamespace(messages=messages_api)
    client = AnthropicModelClient("test-key", "claude-test", sdk_client=sdk)

    reply = client.complete("system", [{"role": "user", "content": "go"}], 128)

    assert reply == "[]"
    assert messages_api.calls == [{
        "model": "claude-test",
        "max_tokens": 128,
        "system": "system",
        "messages": [{"role": "user", "content": "go"}],
    }]


def test_anthropic_adapter_normalizes_sdk_errors():
    sdk = SimpleNamespace(messages=FakeAnthropicMessages(error=RuntimeError("offline")))
    client = AnthropicModelClient("test-key", "claude-test", sdk_client=sdk)

    with pytest.raises(ModelClientError, match="RuntimeError"):
        client.complete("system", [], 128)


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self.payload[:size]


def test_openai_compatible_adapter_normalizes_chat_completion(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        payload = {"choices": [{"message": {"content": "[]"}}]}
        return FakeHTTPResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(model_clients, "urlopen", fake_urlopen)
    client = OpenAICompatibleModelClient(
        "http://localhost:11434/v1", "local-model", api_key="test-key", timeout=5
    )

    reply = client.complete("system", [{"role": "user", "content": "go"}], 128)

    assert reply == "[]"
    request, timeout = requests[0]
    assert request.full_url == "http://localhost:11434/v1/chat/completions"
    assert timeout == 5
    assert request.get_header("Authorization") == "Bearer test-key"
    assert json.loads(request.data) == {
        "model": "local-model",
        "max_completion_tokens": 128,
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "go"},
        ],
    }


def test_openai_compatible_adapter_rejects_invalid_response(monkeypatch):
    monkeypatch.setattr(
        model_clients,
        "urlopen",
        lambda request, timeout: FakeHTTPResponse(b'{"choices": []}'),
    )
    client = OpenAICompatibleModelClient("http://localhost:11434/v1", "local-model")

    with pytest.raises(ModelClientError, match="response"):
        client.complete("system", [], 128)


def test_openai_compatible_adapter_reports_provider_http_error(monkeypatch):
    payload = json.dumps({
        "error": {
            "message": "Unsupported parameter: max_tokens",
            "type": "invalid_request_error",
        }
    }).encode("utf-8")

    def reject(request, timeout):
        raise HTTPError(request.full_url, 400, "Bad Request", {}, BytesIO(payload))

    monkeypatch.setattr(model_clients, "urlopen", reject)
    client = OpenAICompatibleModelClient("https://api.openai.com/v1", "gpt-test")

    with pytest.raises(
        ModelClientError,
        match="HTTP 400: Unsupported parameter: max_tokens",
    ):
        client.complete("system", [], 128)


def test_openai_compatible_adapter_allows_local_server_without_key(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeHTTPResponse(b'{"choices":[{"message":{"content":"[]"}}]}')

    monkeypatch.setattr(model_clients, "urlopen", fake_urlopen)
    client = OpenAICompatibleModelClient("http://localhost:11434/v1", "local-model")

    assert client.complete("system", [], 128) == "[]"
    assert requests[0].get_header("Authorization") is None


def test_openai_compatible_adapter_requires_tls_for_remote_servers():
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAICompatibleModelClient("http://models.example.com/v1", "remote-model")

    OpenAICompatibleModelClient("https://models.example.com/v1", "remote-model")


def test_openai_compatible_adapter_bounds_response_size(monkeypatch):
    monkeypatch.setattr(
        model_clients,
        "urlopen",
        lambda request, timeout: FakeHTTPResponse(
            b"x" * (OpenAICompatibleModelClient.MAX_RESPONSE_BYTES + 1)
        ),
    )
    client = OpenAICompatibleModelClient("http://localhost:11434/v1", "local-model")

    with pytest.raises(ModelClientError, match="size limit"):
        client.complete("system", [], 128)


def test_environment_factory_selects_provider_without_sdk_leaking_to_planner():
    client = build_model_client({
        "AMP_MODEL_PROVIDER": "openai-compatible",
        "OPENAI_BASE_URL": "http://localhost:11434/v1",
        "OPENAI_MODEL": "qwen-test",
    })

    assert isinstance(client, OpenAICompatibleModelClient)


def test_environment_factory_requires_selected_provider_configuration():
    with pytest.raises(ValueError, match="OPENAI_BASE_URL"):
        build_model_client({"AMP_MODEL_PROVIDER": "openai-compatible"})

    with pytest.raises(ValueError, match="Unknown model provider"):
        build_model_client({"AMP_MODEL_PROVIDER": "unknown"})

"""Provider adapters that normalize model completion APIs to plain text."""

import json
import unicodedata
from ipaddress import ip_address
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from anthropic import Anthropic


class ModelClient(Protocol):
    def complete(self, system: str, messages: list[dict], max_tokens: int) -> str:
        """Return one assistant reply as plain text."""


class ModelClientError(RuntimeError):
    """A provider request or response could not be normalized."""


class AnthropicModelClient:
    DEFAULT_MODEL = "claude-opus-4-6"

    def __init__(self, api_key, model=None, sdk_client=None):
        self._model = model or self.DEFAULT_MODEL
        self._sdk_client = sdk_client or Anthropic(api_key=api_key)

    def complete(self, system, messages, max_tokens):
        try:
            response = self._sdk_client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
            return "".join(
                block.text
                for block in response.content
                if getattr(block, "type", None) == "text"
            ).strip()
        except Exception as error:
            raise ModelClientError(error.__class__.__name__) from error


class OpenAICompatibleModelClient:
    MAX_RESPONSE_BYTES = 1_048_576

    def __init__(self, base_url, model, api_key=None, timeout=60):
        self._validate_base_url(base_url)
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        self._timeout = timeout

    @staticmethod
    def _validate_base_url(base_url):
        parsed = urlparse(base_url)
        if parsed.scheme == "https" and parsed.hostname:
            return
        if parsed.scheme == "http" and parsed.hostname:
            try:
                is_loopback = ip_address(parsed.hostname).is_loopback
            except ValueError:
                is_loopback = parsed.hostname.lower() == "localhost"
            if is_loopback:
                return
        raise ValueError(
            "OPENAI_BASE_URL must use HTTPS unless it targets a loopback server"
        )

    def complete(self, system, messages, max_tokens):
        payload = json.dumps({
            "model": self._model,
            "max_completion_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, *messages],
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = Request(self._endpoint, data=payload, headers=headers, method="POST")

        try:
            with urlopen(request, timeout=self._timeout) as response:
                raw = response.read(self.MAX_RESPONSE_BYTES + 1)
            if len(raw) > self.MAX_RESPONSE_BYTES:
                raise ModelClientError("Model response exceeds size limit")
            data = json.loads(raw.decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ModelClientError("Model response content is not text")
            return content.strip()
        except ModelClientError:
            raise
        except HTTPError as error:
            try:
                body = error.read(self.MAX_RESPONSE_BYTES).decode(
                    "utf-8", errors="replace"
                )
                message = json.loads(body).get("error", {}).get("message")
            except (AttributeError, TypeError, ValueError):
                message = None
            if not isinstance(message, str) or not message.strip():
                message = error.reason or "request rejected"
            message = "".join(
                character
                for character in str(message)
                if not unicodedata.category(character).startswith("C")
            )[:500]
            raise ModelClientError(f"HTTP {error.code}: {message}") from error
        except (URLError, TimeoutError, OSError, ValueError, UnicodeError, KeyError,
                IndexError, TypeError) as error:
            raise ModelClientError(
                f"Invalid OpenAI-compatible response: {error.__class__.__name__}"
            ) from error


def build_model_client(environ: Mapping[str, str]):
    """Build the configured provider adapter, or None when credentials are absent."""
    provider = environ.get("AMP_MODEL_PROVIDER", "anthropic").strip().lower()
    if provider == "anthropic":
        api_key = environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        return AnthropicModelClient(api_key, environ.get("ANTHROPIC_MODEL"))

    if provider == "openai-compatible":
        base_url = environ.get("OPENAI_BASE_URL")
        model = environ.get("OPENAI_MODEL")
        missing = [
            name for name, value in (
                ("OPENAI_BASE_URL", base_url), ("OPENAI_MODEL", model)
            ) if not value
        ]
        if missing:
            raise ValueError(
                "Missing model configuration: " + ", ".join(missing)
            )
        return OpenAICompatibleModelClient(
            base_url,
            model,
            api_key=environ.get("OPENAI_API_KEY"),
        )

    raise ValueError(f"Unknown model provider: {provider}")

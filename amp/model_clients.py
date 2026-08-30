"""Provider adapters that normalize model completion APIs to plain text."""

# imports
import json
import unicodedata
from ipaddress import ip_address
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from anthropic import Anthropic


"""
--------------------------------------------------------------------------------------------
Class Header - Model client contract
--------------------------------------------------------------------------------------------
The whole provider surface, one method returning plain text. Deliberately this small because
everything above it, the planner especially, should not know which provider answered. Tool
calling, streaming and structured output all differ per provider, so none of them are in the
contract, the planner asks for text and parses JSON out of it itself.

A typing Protocol rather than a base class, so adapters match structurally and a test fake
does not have to inherit anything to stand in.

ModelClientError is the one exception type callers handle. Every provider failure normalizes
into it so the planner has one thing to catch rather than an SDK exception tree per provider.
--------------------------------------------------------------------------------------------
"""
class ModelClient(Protocol):

    def complete(self, system: str, messages: list[dict], max_tokens: int) -> str: ...


# The single exception every provider failure normalizes into, so the planner catches one type
# rather than an SDK error tree per provider.
class ModelClientError(RuntimeError):

    pass


"""
--------------------------------------------------------------------------------------------
Class Header - Anthropic adapter
--------------------------------------------------------------------------------------------
Wraps the official SDK. sdk_client is injectable so tests can pass a fake without an API key
or a network call, and so the SDK is only constructed when it is actually going to be used.

The response is a list of content blocks, not a string, so text blocks are filtered and joined
and anything else is dropped. getattr with a default is used rather than attribute access
because block shapes vary across SDK versions and a missing attribute should skip the block
rather than crash the reply.

Catches bare Exception and re-raises only the class name. The SDK raises its own tree of
errors, and their messages can contain the request body, which means prompt content and
occasionally the key. The class name says what went wrong without carrying any of that.
--------------------------------------------------------------------------------------------
"""
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
            # replies are content blocks, keep the text ones and ignore anything else
            return "".join(block.text for block in response.content if getattr(block, "type", None) == "text").strip()

        except Exception as error:
            # class name only, SDK messages can echo the prompt and credentials back
            raise ModelClientError(error.__class__.__name__) from error


"""
--------------------------------------------------------------------------------------------
Class Header - OpenAI-compatible adapter
--------------------------------------------------------------------------------------------
Speaks the Chat Completions shape over the standard library rather than an SDK, because the
point of this adapter is to work against anything that implements the shape, Ollama, LM Studio
and vLLM included. Pulling in a vendor SDK to talk to a local server would be backwards.

MAX_RESPONSE_BYTES exists because the endpoint is user-configured and may not be trustworthy.
Reading one byte past the cap is how the limit is detected, a read of exactly the cap cannot
tell you whether more was waiting.
--------------------------------------------------------------------------------------------
"""
class OpenAICompatibleModelClient:

    MAX_RESPONSE_BYTES = 1_048_576

    def __init__(self, base_url, model, api_key=None, timeout=60):
        self._validate_base_url(base_url)
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        self._timeout = timeout


    """
    --------------------------------------------------------------------------------------------
    Function Header - Base URL policy
    --------------------------------------------------------------------------------------------
    HTTPS anywhere, HTTP only to loopback. The request carries an API key in a header, so plain
    HTTP to a remote host would put that key on the wire in clear text. Loopback is exempt
    because traffic never leaves the machine, and local model servers rarely offer TLS.

    Loopback is decided by parsing the host as an IP first, which catches 127.0.0.1, ::1 and
    the whole 127.0.0.0/8 range. The ValueError fallback is for hostnames, where "localhost" is
    the only name accepted, since any other name could resolve anywhere.
    --------------------------------------------------------------------------------------------
    """
    @staticmethod
    def _validate_base_url(base_url):
        parsed = urlparse(base_url)

        if parsed.scheme == "https" and parsed.hostname:
            return

        if parsed.scheme == "http" and parsed.hostname:
            # parse as an address first, that covers the whole 127.0.0.0/8 range and ::1
            try:
                is_loopback = ip_address(parsed.hostname).is_loopback
            except ValueError:
                # not an address, so only the literal name is accepted
                is_loopback = parsed.hostname.lower() == "localhost"

            if is_loopback:
                return

        raise ValueError("OPENAI_BASE_URL must use HTTPS unless it targets a loopback server")


    """
    --------------------------------------------------------------------------------------------
    Function Header - Completion request
    --------------------------------------------------------------------------------------------
    Builds the request, sends it, and reduces whatever comes back to one text string.

    The system prompt is prepended as a message rather than sent as its own field, which is how
    the Chat Completions shape carries it, unlike Anthropic where system is separate.

    The error handling is layered on purpose. ModelClientError is re-raised untouched so an
    error raised inside the try is not rewrapped by the handlers below it. HTTPError is treated
    specially because its body usually contains a real explanation from the provider, which is
    worth surfacing. Everything else collapses to a class name, since a malformed response has
    no message worth trusting.

    The message from an HTTP error is sanitized before it is used. It is remote text that ends
    up in logs and in planner context, so control characters are stripped, which removes ANSI
    escapes and anything that could forge log lines, and it is cut to 500 characters so a
    provider cannot flood the log through an error path.
    --------------------------------------------------------------------------------------------
    """
    def complete(self, system, messages, max_tokens):
        # system goes in as the first message here, unlike Anthropic where it is its own field
        payload = json.dumps({
            "model": self._model,
            "max_completion_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, *messages],
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}

        # local servers usually need no key, so the header is only sent when one exists
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        request = Request(self._endpoint, data=payload, headers=headers, method="POST")

        try:
            with urlopen(request, timeout=self._timeout) as response:
                # one byte past the cap, so hitting the limit is distinguishable from filling it
                raw = response.read(self.MAX_RESPONSE_BYTES + 1)

            if len(raw) > self.MAX_RESPONSE_BYTES:
                raise ModelClientError("Model response exceeds size limit")

            data = json.loads(raw.decode("utf-8"))
            content = data["choices"][0]["message"]["content"]

            if not isinstance(content, str):
                raise ModelClientError("Model response content is not text")

            return content.strip()

        # raised above, so let it through rather than rewrapping it below
        except ModelClientError:
            raise

        except HTTPError as error:
            # the body usually carries the provider's real explanation, worth surfacing
            try:
                body = error.read(self.MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
                message = json.loads(body).get("error", {}).get("message")

            except (AttributeError, TypeError, ValueError):
                message = None

            if not isinstance(message, str) or not message.strip():
                message = error.reason or "request rejected"

            # remote text heading for logs, so drop control characters and cap the length
            message = "".join(character for character in str(message) if not unicodedata.category(character).startswith("C"))[:500]
            raise ModelClientError(f"HTTP {error.code}: {message}") from error

        # a malformed response has nothing worth quoting, so only the failure kind is kept
        except (URLError, TimeoutError, OSError, ValueError, UnicodeError, KeyError,IndexError, TypeError) as error:
            raise ModelClientError(f"Invalid OpenAI-compatible response: {error.__class__.__name__}") from error


"""
--------------------------------------------------------------------------------------------
Function Header - Provider selection
--------------------------------------------------------------------------------------------
Reads the environment and builds whichever adapter is configured. Anthropic is the default so
that setting one API key is enough to get running.

The two failure modes are deliberately different. A missing Anthropic key returns None, which
means "no provider configured" and lets idle mode connect without one. Missing OpenAI settings
raise, because naming that provider is an explicit choice and a half-configured endpoint is a
mistake worth reporting rather than silently treating as absent.

Both missing OpenAI variables are collected before raising, so one message names everything
that needs fixing instead of revealing them one run at a time.
--------------------------------------------------------------------------------------------
"""
def build_model_client(environ: Mapping[str, str]):
    provider = environ.get("AMP_MODEL_PROVIDER", "anthropic").strip().lower()

    if provider == "anthropic":
        api_key = environ.get("ANTHROPIC_API_KEY")

        # None rather than raising, unconfigured is a valid state for idle mode
        if not api_key:
            return None

        return AnthropicModelClient(api_key, environ.get("ANTHROPIC_MODEL"))

    if provider == "openai-compatible":
        base_url = environ.get("OPENAI_BASE_URL")
        model = environ.get("OPENAI_MODEL")
        # collect both, so one message names everything missing instead of one per run
        missing = [name for name, value in (("OPENAI_BASE_URL", base_url), ("OPENAI_MODEL", model)) if not value]

        # naming this provider is explicit, so half-configured is an error, not "absent"
        if missing:
            raise ValueError("Missing model configuration: " + ", ".join(missing))

        return OpenAICompatibleModelClient(base_url, model, api_key=environ.get("OPENAI_API_KEY"))

    raise ValueError(f"Unknown model provider: {provider}")

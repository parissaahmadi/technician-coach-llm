"""Language-model clients. Both implement ``complete(system, user) -> str``.

* ``MockLLM``: deterministic, plays the model's part from the same context a real model would get.
* ``AnthropicHTTP``: the Messages API over plain ``urllib`` (no SDK), with retry and backoff.
"""

from __future__ import annotations

import json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Protocol

from . import heuristics, prompts
from .errors import LLMBadRequest, LLMBadResponse, LLMUnavailable

DEFAULT_MODEL = "claude-sonnet-5-5"
API_VERSION = "2023-06-01"
MAX_BACKOFF = 30.0
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class MockLLM:
    """Needs no network and no key. Same input, same output."""

    name = "mock"

    def complete(self, system: str, user: str) -> str:
        task = user.split("\n", 1)[0].removeprefix("TASK:").strip()
        ctx = prompts.extract_context(user)
        if task == "coach":
            return json.dumps(heuristics.suggest(ctx), ensure_ascii=False)
        if task == "assessment":
            return json.dumps(heuristics.mock_narrative(ctx), ensure_ascii=False)
        raise LLMBadResponse(f"mock does not know task {task!r}")


class AnthropicHTTP:
    """Minimal Messages API client. Retries 429, 5xx, timeouts and connection errors with
    exponential backoff and jitter (honouring ``retry-after``); never retries other 4xx."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, base_url: str = "https://api.anthropic.com",
                 *, timeout: float = 30.0, max_retries: int = 3, max_tokens: int = 800,
                 sleep: Callable[[float], None] = time.sleep,
                 rng: Callable[[], float] = random.random, trust_env_proxy: bool = True) -> None:  # fmt: skip
        if not api_key or not api_key.strip():
            raise LLMBadRequest("no API key: set ANTHROPIC_API_KEY or use --llm mock")
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in _LOOPBACK):
            raise LLMBadRequest("base_url must be https (plain http only for localhost)")
        self._key = api_key.strip()
        self.model = model
        self.name = f"anthropic:{model}"
        self.url = base_url.rstrip("/") + "/v1/messages"
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self._sleep = sleep
        self._rng = rng
        handler = urllib.request.ProxyHandler() if trust_env_proxy else urllib.request.ProxyHandler({})
        self._opener = urllib.request.build_opener(handler)

    # ------------------------------------------------------------------ internals
    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***")[:200]

    def _delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(MAX_BACKOFF, max(0.0, float(retry_after)))
            except ValueError:
                pass
        return min(MAX_BACKOFF, 0.5 * 2**attempt) * (0.5 + self._rng())

    def _post(self, payload: bytes) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(
            self.url, data=payload, method="POST",
            headers={"content-type": "application/json", "x-api-key": self._key,
                     "anthropic-version": API_VERSION},
        )  # fmt: skip
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), e.read()

    # ------------------------------------------------------------------ public
    def complete(self, system: str, user: str) -> str:
        payload = json.dumps({
            "model": self.model, "max_tokens": self.max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")  # fmt: skip
        last = "no attempt made"
        for attempt in range(self.max_retries + 1):
            retry_after = None
            try:
                status, headers, body = self._post(payload)
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as e:
                last = f"network error: {type(e).__name__}"
            else:
                if status == 200:
                    return self._text(body)
                text = self._scrub(body.decode("utf-8", "replace"))
                if status == 429 or status >= 500:
                    last = f"HTTP {status}"
                    retry_after = {k.lower(): v for k, v in headers.items()}.get("retry-after")
                else:
                    raise LLMBadRequest(f"HTTP {status}: {text}")
            if attempt < self.max_retries:
                self._sleep(self._delay(attempt, retry_after))
        raise LLMUnavailable(f"model service unavailable after {self.max_retries + 1} attempts ({last})")

    def _text(self, body: bytes) -> str:
        try:
            data = json.loads(body)
            blocks = data["content"]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        except (ValueError, KeyError, TypeError, AttributeError):
            raise LLMBadResponse("response is not a Messages API reply") from None
        if not text.strip():
            raise LLMBadResponse("response has no text")
        return text

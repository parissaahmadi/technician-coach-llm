import json
import socket

import pytest
from fake_server import FakeAPI, ok

from technician_coach.errors import LLMBadRequest, LLMBadResponse, LLMUnavailable
from technician_coach.llm import API_VERSION, AnthropicHTTP, MockLLM

KEY = "sk-test-SECRET-123"


def client(url, sleeps=None, **kw):
    sleeps = sleeps if sleeps is not None else []
    kw.setdefault("rng", lambda: 0.5)  # jitter factor exactly 1.0
    return AnthropicHTTP(KEY, "model-x", url, sleep=sleeps.append, trust_env_proxy=False, **kw), sleeps


def test_request_shape_and_text_extraction():
    with FakeAPI([(200, {}, {"content": [{"type": "text", "text": "he"}, {"type": "tool_use", "id": "t"},
                                         {"type": "text", "text": "llo"}]}, 0)]) as api:
        c, _ = client(api.url, max_tokens=123)
        assert c.complete("SYS", "USER") == "hello"
    req = api.requests[0]
    assert req["path"] == "/v1/messages"
    assert req["headers"]["x-api-key"] == KEY and req["headers"]["anthropic-version"] == API_VERSION
    assert req["headers"]["content-type"] == "application/json"
    assert req["json"] == {"model": "model-x", "max_tokens": 123, "system": "SYS",
                           "messages": [{"role": "user", "content": "USER"}]}  # fmt: skip


def test_429_is_retried_and_retry_after_is_honoured():
    with FakeAPI([(429, {"retry-after": "2"}, {"error": "slow down"}, 0), ok("fine")]) as api:
        c, sleeps = client(api.url)
        assert c.complete("s", "u") == "fine"
    assert sleeps == [2.0] and len(api.requests) == 2


def test_5xx_backs_off_exponentially_then_succeeds():
    with FakeAPI([(500, {}, b"boom", 0), (503, {}, b"boom", 0), (529, {}, b"busy", 0), ok("late")]) as api:
        c, sleeps = client(api.url)
        assert c.complete("s", "u") == "late"
    assert sleeps == [0.5, 1.0, 2.0]


def test_retries_are_bounded_and_end_in_unavailable():
    with FakeAPI([(500, {}, b"boom", 0)]) as api:
        c, sleeps = client(api.url, max_retries=2)
        with pytest.raises(LLMUnavailable, match="3 attempts"):
            c.complete("s", "u")
    assert len(api.requests) == 3 and len(sleeps) == 2


def test_backoff_is_capped_and_jittered():
    c, _ = client("http://127.0.0.1:1")
    assert c._delay(10, None) == 30.0  # 0.5 * 2**10 = 512 -> capped
    low = AnthropicHTTP(KEY, base_url="http://127.0.0.1:1", rng=lambda: 0.0, trust_env_proxy=False)
    high = AnthropicHTTP(KEY, base_url="http://127.0.0.1:1", rng=lambda: 1.0, trust_env_proxy=False)
    assert (low._delay(1, None), high._delay(1, None)) == (0.5, 1.5)
    assert c._delay(0, "garbage") == 0.5  # unusable retry-after falls back to backoff
    assert c._delay(0, "9999") == 30.0  # retry-after is capped too


def test_other_4xx_is_not_retried_and_never_echoes_the_key():
    with FakeAPI([(401, {}, f"invalid x-api-key {KEY} for this account".encode(), 0)]) as api:
        c, sleeps = client(api.url)
        with pytest.raises(LLMBadRequest) as e:
            c.complete("s", "u")
    assert "401" in str(e.value) and KEY not in str(e.value) and "***" in str(e.value)
    assert sleeps == [] and len(api.requests) == 1


def test_connection_refused_is_unavailable_after_retries():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    c, sleeps = client(f"http://127.0.0.1:{port}", max_retries=1)
    with pytest.raises(LLMUnavailable, match="network error"):
        c.complete("s", "u")
    assert len(sleeps) == 1


def test_timeout_is_retried_then_unavailable():
    with FakeAPI([(200, {}, {"content": [{"type": "text", "text": "x"}]}, 0.6)]) as api:
        c, sleeps = client(api.url, timeout=0.15, max_retries=1)
        with pytest.raises(LLMUnavailable):
            c.complete("s", "u")
    assert len(sleeps) == 1


@pytest.mark.parametrize(
    "body",
    [b"not json", b'{"content": []}', b'{"content": [{"type": "text", "text": "   "}]}',
     b'{"nope": 1}', b'{"content": "text"}', b"[1, 2]"],
)
def test_unusable_200_bodies_are_bad_responses(body):
    with FakeAPI([(200, {}, body, 0)]) as api:
        c, _ = client(api.url)
        with pytest.raises(LLMBadResponse):
            c.complete("s", "u")


@pytest.mark.parametrize("url", ["http://example.com", "ftp://127.0.0.1", "example.com"])
def test_plain_http_is_only_allowed_for_localhost(url):
    with pytest.raises(LLMBadRequest, match="https"):
        AnthropicHTTP(KEY, base_url=url)
    AnthropicHTTP(KEY, base_url="https://api.example.com")  # https is fine anywhere


@pytest.mark.parametrize("key", ["", "   ", None])
def test_missing_key_fails_fast_with_a_helpful_message(key):
    with pytest.raises(LLMBadRequest, match="ANTHROPIC_API_KEY"):
        AnthropicHTTP(key)


def test_mock_is_deterministic_and_rejects_unknown_tasks():
    from technician_coach import prompts

    prompt = prompts.render("coach", {"causes": [], "checks": [], "done": [], "loto": True, "symptom": "s",
                                      "safety": {"loto": "L", "extras": []}, "notes": []})  # fmt: skip
    m = MockLLM()
    assert m.complete("sys", prompt) == m.complete("other system", prompt)
    assert json.loads(m.complete("", prompt))["escalate"] is True  # no live causes
    with pytest.raises(LLMBadResponse):
        m.complete("", prompts.render("poetry", {}))

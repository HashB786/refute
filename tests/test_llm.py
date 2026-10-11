"""The LLM layer: config, cache, offline, throttle, retries and FakeLLM.

No test here opens a socket. Requests are served by an httpx MockTransport,
and the throttle runs on a fake clock so nothing waits.
"""

from __future__ import annotations

import json

import httpx
import pytest

from refute.llm import (
    DEFAULT_MAX_RPM,
    DEFAULT_MAX_TOKENS,
    RESERVED_OUTPUT_TOKENS,
    Cache,
    CacheMiss,
    Client,
    FakeLLM,
    LLMError,
    Settings,
    Throttle,
    estimate_tokens,
    load_config,
    parse_env_file,
)
from refute.models import LLMResponse, Usage


# ---------------------------------------------------------------------------
# a clock the tests drive by hand


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def settings(**overrides) -> Settings:
    base = dict(
        provider="openai",
        base_url="https://example.invalid/v1",
        api_key="test-key",
        model="test-model",
    )
    base.update(overrides)
    return Settings(**base)


def openai_reply(text: str, finish: str = "stop", prompt: int = 11, completion: int = 7):
    return {
        "model": "test-model",
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


# ---------------------------------------------------------------------------
# .env parsing


def test_parse_env_file_handles_comments_quotes_and_export():
    text = (
        "# a comment\n"
        "\n"
        "REFUTE_PROVIDER=anthropic\n"
        "export REFUTE_MODEL=\"claude-test\"\n"
        "REFUTE_API_KEY='secret-value'\n"
        "  REFUTE_MAX_RPM = 5  \n"
        "not a pair\n"
    )
    values = parse_env_file(text)
    assert values["REFUTE_PROVIDER"] == "anthropic"
    assert values["REFUTE_MODEL"] == "claude-test"
    assert values["REFUTE_API_KEY"] == "secret-value"
    assert values["REFUTE_MAX_RPM"] == "5"
    assert "not a pair" not in values


def test_parse_env_file_keeps_an_equals_sign_inside_the_value():
    assert parse_env_file("REFUTE_API_KEY=ab==cd")["REFUTE_API_KEY"] == "ab==cd"


def test_a_real_environment_variable_beats_the_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "REFUTE_MODEL=from-file\nREFUTE_API_KEY=file-key\n", encoding="utf-8"
    )
    config = load_config(env_file, environ={"REFUTE_MODEL": "from-environ"})
    assert config["REFUTE_MODEL"] == "from-environ"
    assert config["REFUTE_API_KEY"] == "file-key"


def test_load_config_ignores_unrelated_environment_variables(tmp_path):
    config = load_config(tmp_path / "missing.env", environ={"PATH": "/usr/bin"})
    assert config == {}


def test_settings_defaults_and_validation():
    resolved = Settings.from_config({"REFUTE_MODEL": "m"})
    assert resolved.provider == "openai"
    assert resolved.base_url == "https://api.openai.com/v1"
    assert resolved.max_tokens == DEFAULT_MAX_TOKENS
    assert resolved.max_rpm == DEFAULT_MAX_RPM

    anthropic = Settings.from_config({"REFUTE_PROVIDER": "Anthropic"})
    assert anthropic.base_url == "https://api.anthropic.com"

    with pytest.raises(LLMError):
        Settings.from_config({"REFUTE_PROVIDER": "gemini"})
    with pytest.raises(LLMError):
        Settings.from_config({"REFUTE_MAX_RPM": "lots"})


def test_a_custom_base_url_loses_its_trailing_slash():
    resolved = Settings.from_config({"REFUTE_BASE_URL": "http://localhost:8000/v1/"})
    assert resolved.base_url == "http://localhost:8000/v1"


# ---------------------------------------------------------------------------
# cache


def test_cache_miss_then_hit(tmp_path):
    cache = Cache(tmp_path)
    assert cache.get("oracle_a", "prompt") is None
    stored = LLMResponse(
        text="code",
        purpose="oracle_a",
        model="test-model",
        provider="openai",
        usage=Usage(prompt_tokens=3, completion_tokens=4),
    )
    cache.put("oracle_a", "prompt", stored)
    loaded = cache.get("oracle_a", "prompt")
    assert loaded is not None
    assert loaded.text == "code"
    assert loaded.cached
    assert loaded.usage.completion_tokens == 4


def test_the_cache_key_separates_purpose_from_prompt(tmp_path):
    cache = Cache(tmp_path)
    assert cache.key("oracle_a", "x") != cache.key("oracle_b", "x")
    assert cache.key("oracle_a", "x") == cache.key("oracle_a", "x")
    # purpose + prompt must not collide by concatenation
    assert cache.key("oracle_a", "b") != cache.key("oracle_ab", "")


def test_a_corrupt_cache_file_is_treated_as_a_miss(tmp_path):
    cache = Cache(tmp_path)
    path = tmp_path / (cache.key("validator", "p") + ".json")
    tmp_path.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert cache.get("validator", "p") is None


def test_a_cached_call_makes_no_request(tmp_path):
    def refuse(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("a cached prompt must not reach the network")

    client = Client(
        settings(),
        cache_dir=tmp_path,
        transport=httpx.MockTransport(refuse),
    )
    client.cache.put(
        "generator",
        "gen prompt",
        LLMResponse(
            text="gen code",
            purpose="generator",
            model="test-model",
            provider="openai",
            usage=Usage(1, 1),
        ),
    )
    response = client.complete("generator", "gen prompt")
    assert response.text == "gen code"
    assert response.cached


def test_the_api_key_is_never_written_into_the_cache(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=openai_reply("ok"))

    client = Client(
        settings(api_key="super-secret-key"),
        cache_dir=tmp_path,
        transport=httpx.MockTransport(handler),
    )
    client.complete("samples", "hello")
    written = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))
    assert "super-secret-key" not in written
    assert calls and calls[0].headers["authorization"] == "Bearer super-secret-key"


# ---------------------------------------------------------------------------
# offline


def test_offline_raises_a_clear_error_on_a_miss(tmp_path):
    client = Client(settings(), cache_dir=tmp_path, offline=True)
    with pytest.raises(CacheMiss) as caught:
        client.complete("oracle_a", "unseen prompt")
    message = str(caught.value)
    assert "--offline" in message
    assert "oracle_a" in message
    assert str(tmp_path) in message


def test_offline_serves_a_hit_without_a_key(tmp_path):
    client = Client(settings(api_key=""), cache_dir=tmp_path, offline=True)
    client.cache.put(
        "fix",
        "p",
        LLMResponse(
            text="patched", purpose="fix", model="m", provider="openai", usage=Usage()
        ),
    )
    assert client.complete("fix", "p").text == "patched"


def test_an_unknown_purpose_is_refused(tmp_path):
    client = Client(settings(), cache_dir=tmp_path)
    with pytest.raises(LLMError):
        client.complete("hack", "p")


def test_a_missing_key_or_model_is_reported_before_any_request(tmp_path):
    with pytest.raises(LLMError) as caught:
        Client(settings(api_key=""), cache_dir=tmp_path).complete("samples", "p")
    assert "REFUTE_API_KEY" in str(caught.value)

    with pytest.raises(LLMError) as caught:
        Client(settings(model=""), cache_dir=tmp_path).complete("samples", "p")
    assert "REFUTE_MODEL" in str(caught.value)


# ---------------------------------------------------------------------------
# throttle


def test_the_throttle_lets_calls_through_while_both_limits_allow_it():
    clock = FakeClock()
    throttle = Throttle(max_rpm=5, max_tpm=100000, clock=clock, sleeper=clock.sleep)
    for _ in range(5):
        assert throttle.reserve(10) == 0.0
    assert clock.slept == []


def test_the_request_limit_waits_for_the_window_to_slide():
    clock = FakeClock()
    throttle = Throttle(max_rpm=2, max_tpm=1000000, clock=clock, sleeper=clock.sleep)
    throttle.reserve(1)
    clock.now += 10
    throttle.reserve(1)
    waited = throttle.reserve(1)
    # The oldest request was 10 s ago, so 50 s of its window remain.
    assert waited == pytest.approx(50.0)
    assert throttle.waited == pytest.approx(50.0)


def test_the_token_limit_waits_even_when_requests_are_free():
    clock = FakeClock()
    throttle = Throttle(max_rpm=100, max_tpm=3000, clock=clock, sleeper=clock.sleep)
    assert throttle.reserve(500) == 0.0
    waited = throttle.reserve(500)
    # One reservation is 500 + 1500 reserved output tokens, so a second one
    # does not fit under 3000 until the first leaves the window.
    assert waited == pytest.approx(60.0)


def test_recording_real_usage_frees_the_reserved_output_tokens():
    clock = FakeClock()
    throttle = Throttle(max_rpm=100, max_tpm=4000, clock=clock, sleeper=clock.sleep)
    throttle.reserve(500)
    throttle.record(200)
    # Only 200 tokens are now charged, so the next call fits straight away.
    assert throttle.reserve(500) == 0.0


def test_the_window_forgets_old_calls():
    clock = FakeClock()
    throttle = Throttle(max_rpm=1, max_tpm=10000, clock=clock, sleeper=clock.sleep)
    throttle.reserve(1)
    clock.now += 61
    assert throttle.reserve(1) == 0.0


def test_a_prompt_larger_than_the_whole_window_still_goes_through():
    clock = FakeClock()
    throttle = Throttle(max_rpm=100, max_tpm=1000, clock=clock, sleeper=clock.sleep)
    throttle.reserve(1)
    waited = throttle.reserve(50000)
    assert waited >= 0.0  # it waits out the window rather than stalling forever


def test_reserved_output_tokens_are_part_of_the_estimate():
    clock = FakeClock()
    throttle = Throttle(
        max_rpm=100, max_tpm=RESERVED_OUTPUT_TOKENS + 10, clock=clock, sleeper=clock.sleep
    )
    assert throttle.reserve(5) == 0.0
    assert throttle.reserve(5) > 0.0


def test_estimate_tokens_is_about_a_third_of_the_characters():
    assert estimate_tokens("") == 1
    assert estimate_tokens("x" * 300) == 100


# ---------------------------------------------------------------------------
# requests, retries and providers


def test_an_openai_call_sends_the_expected_payload(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=openai_reply("brute force"),
            headers={"x-ratelimit-remaining-requests": "19", "server": "test"},
        )

    client = Client(
        settings(), cache_dir=tmp_path, transport=httpx.MockTransport(handler)
    )
    response = client.complete("oracle_a", "write a brute force", system="be exact")
    assert seen["url"] == "https://example.invalid/v1/chat/completions"
    assert seen["body"]["temperature"] == 0.2
    assert seen["body"]["messages"][0] == {"role": "system", "content": "be exact"}
    assert response.text == "brute force"
    assert response.usage.prompt_tokens == 11
    assert not response.usage.estimated
    assert response.headers == {"x-ratelimit-remaining-requests": "19"}
    assert client.calls == 1


def test_an_anthropic_call_uses_the_messages_endpoint(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "claude-test",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        )

    client = Client(
        settings(provider="anthropic", base_url="https://example.invalid"),
        cache_dir=tmp_path,
        transport=httpx.MockTransport(handler),
    )
    response = client.complete("validator", "write a validator", system="rules")
    assert seen["url"] == "https://example.invalid/v1/messages"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["system"] == "rules"
    assert response.text == "ok"
    assert response.usage.prompt_tokens == 12


def test_a_missing_usage_block_falls_back_to_an_estimate(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "x" * 300}}]}
        )

    client = Client(
        settings(), cache_dir=tmp_path, transport=httpx.MockTransport(handler)
    )
    response = client.complete("samples", "p")
    assert response.usage.estimated
    assert response.usage.completion_tokens == 100


def test_a_truncated_reply_counts_as_a_failed_attempt(tmp_path):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(200, json=openai_reply("half a scr", finish="length"))
        return httpx.Response(200, json=openai_reply("the whole script"))

    clock = FakeClock()
    client = Client(
        settings(),
        cache_dir=tmp_path,
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleeper=clock.sleep,
    )
    response = client.complete("oracle_b", "p")
    assert len(attempts) == 2
    assert response.text == "the whole script"


def test_a_reply_that_is_always_truncated_fails_with_the_reason(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=openai_reply("cut", finish="length"))

    clock = FakeClock()
    client = Client(
        settings(),
        cache_dir=tmp_path,
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleeper=clock.sleep,
    )
    with pytest.raises(LLMError) as caught:
        client.complete("fix", "p")
    assert "REFUTE_MAX_TOKENS" in str(caught.value)
    assert not list(tmp_path.glob("*.json"))  # a failure is never cached


def test_a_429_honors_retry_after(tmp_path):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"retry-after": "7"}, json={})
        return httpx.Response(200, json=openai_reply("done"))

    clock = FakeClock()
    client = Client(
        settings(),
        cache_dir=tmp_path,
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleeper=clock.sleep,
    )
    assert client.complete("samples", "p").text == "done"
    assert 7.0 in clock.slept


def test_a_500_backs_off_and_retries(tmp_path):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, text="upstream down")
        return httpx.Response(200, json=openai_reply("recovered"))

    clock = FakeClock()
    client = Client(
        settings(),
        cache_dir=tmp_path,
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleeper=clock.sleep,
    )
    assert client.complete("samples", "p").text == "recovered"
    assert len(clock.slept) == 2
    assert clock.slept[1] > clock.slept[0]


def test_reasoning_effort_is_dropped_after_a_400_and_not_sent_again(tmp_path):
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, text="unknown parameter reasoning_effort")
        return httpx.Response(200, json=openai_reply("ok"))

    clock = FakeClock()
    client = Client(
        settings(reasoning_effort="high"),
        cache_dir=tmp_path,
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleeper=clock.sleep,
    )
    assert client.complete("oracle_a", "first").text == "ok"
    assert client.complete("oracle_b", "second").text == "ok"
    assert "reasoning_effort" in bodies[0]
    assert all("reasoning_effort" not in body for body in bodies[1:])


def test_a_400_without_reasoning_effort_fails_immediately(tmp_path):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, text="bad model name")

    client = Client(
        settings(), cache_dir=tmp_path, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(LLMError) as caught:
        client.complete("samples", "p")
    assert "400" in str(caught.value)
    assert "bad model name" in str(caught.value)
    assert len(attempts) == 1


def test_a_transport_error_is_retried_then_reported(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    clock = FakeClock()
    client = Client(
        settings(),
        cache_dir=tmp_path,
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleeper=clock.sleep,
    )
    with pytest.raises(LLMError) as caught:
        client.complete("samples", "p")
    assert "ConnectError" in str(caught.value)


def test_rate_limit_headers_are_filtered_and_lowercased():
    headers = httpx.Headers(
        {
            "X-RateLimit-Limit-Requests": "20",
            "X-RateLimit-Remaining-Tokens": "5000",
            "Content-Type": "application/json",
        }
    )
    picked = Client.rate_limit_headers(headers)
    assert picked == {
        "x-ratelimit-limit-requests": "20",
        "x-ratelimit-remaining-tokens": "5000",
    }


# ---------------------------------------------------------------------------
# FakeLLM


def test_fake_llm_answers_by_purpose():
    fake = FakeLLM({"oracle_a": "a code", "oracle_b": "b code"})
    assert fake.complete("oracle_a", "p").text == "a code"
    assert fake.complete("oracle_b", "p").text == "b code"
    assert fake.calls == 2
    assert [purpose for purpose, _ in fake.prompts] == ["oracle_a", "oracle_b"]


def test_fake_llm_refuses_a_purpose_it_was_not_given():
    with pytest.raises(LLMError):
        FakeLLM().complete("validator", "p")
    with pytest.raises(LLMError):
        FakeLLM({"x": "y"}).complete("x", "p")


def test_fake_llm_can_reply_differently_to_each_repair_round():
    fake = FakeLLM({"generator": ["broken", "fixed"]})
    assert fake.complete("generator", "p").text == "broken"
    assert fake.complete("generator", "p").text == "fixed"


def test_fake_llm_can_answer_from_the_prompt():
    fake = FakeLLM({"fix": lambda prompt: "saw: " + prompt[:3]})
    assert fake.complete("fix", "abcdef").text == "saw: abc"


def test_use_cache_false_forces_a_real_call_and_stores_nothing(tmp_path):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(200, json=openai_reply("live"))

    client = Client(
        settings(), cache_dir=tmp_path, transport=httpx.MockTransport(handler)
    )
    client.cache.put(
        "samples",
        "p",
        LLMResponse(
            text="stale", purpose="samples", model="m", provider="openai", usage=Usage()
        ),
    )
    response = client.complete("samples", "p", use_cache=False)
    assert response.text == "live"
    assert len(attempts) == 1
    # The stale entry is left alone rather than overwritten by a ping.
    assert client.cache.get("samples", "p").text == "stale"


def test_use_cache_false_still_reads_the_cache_when_offline(tmp_path):
    client = Client(settings(api_key=""), cache_dir=tmp_path, offline=True)
    client.cache.put(
        "samples",
        "p",
        LLMResponse(
            text="recorded",
            purpose="samples",
            model="m",
            provider="openai",
            usage=Usage(),
        ),
    )
    assert client.complete("samples", "p", use_cache=False).text == "recorded"


def test_a_cached_call_still_reports_what_it_cost(tmp_path):
    # A replayed run should not claim its tooling was free: the usage stored
    # with the recording is what the call cost when it was made.
    client = Client(settings(), cache_dir=tmp_path)
    client.cache.put(
        "oracle_a",
        "p",
        LLMResponse(
            text="code",
            purpose="oracle_a",
            model="m",
            provider="openai",
            usage=Usage(prompt_tokens=400, completion_tokens=120),
        ),
    )
    response = client.complete("oracle_a", "p")
    assert response.cached
    assert client.cached == 1
    assert client.calls == 0
    assert client.tokens == 520

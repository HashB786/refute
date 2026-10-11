"""The only place Refute talks to a language model.

Two providers are supported: any OpenAI-compatible ``/chat/completions``
endpoint, and Anthropic's ``/v1/messages``. Every call carries a ``purpose``,
is throttled by both requests and tokens, is retried on 429 and 5xx, and is
cached on disk so a recorded run replays without a key.

Keys are read but never logged, printed or written into the cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import httpx

from .models import LLMResponse, PURPOSES, Usage

DEFAULT_MAX_TOKENS = 3000
DEFAULT_MAX_RPM = 20
DEFAULT_MAX_TPM = 6000
RESERVED_OUTPUT_TOKENS = 1500
TEMPERATURE = 0.2
MAX_ATTEMPTS = 4
WINDOW = 60.0

_DEFAULT_BASE_URL = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}


class LLMError(Exception):
    """A model call could not be completed. The message is safe to print."""


class CacheMiss(LLMError):
    """--offline was given and this prompt is not in the cache."""


def estimate_tokens(text: str) -> int:
    """A deliberately rough token estimate, used before a call and on no usage."""
    return max(1, len(text) // 3)


# ---------------------------------------------------------------------------
# configuration


def parse_env_file(text: str) -> dict[str, str]:
    """Read KEY=VALUE lines. No dependency, and no surprises.

    Blank lines and ``#`` comments are skipped, an ``export`` prefix is
    tolerated, and one layer of matching quotes is stripped.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_config(
    env_path: Path | None = None, environ: dict[str, str] | None = None
) -> dict[str, str]:
    """Merge ./.env with the real environment. Real environment variables win."""
    environ = dict(os.environ if environ is None else environ)
    path = Path(".env") if env_path is None else env_path
    config: dict[str, str] = {}
    if path.is_file():
        config.update(parse_env_file(path.read_text(encoding="utf-8")))
    for key, value in environ.items():
        if key.startswith("REFUTE_"):
            config[key] = value
    return config


@dataclass
class Settings:
    """Everything the client needs, resolved once from config."""

    provider: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    max_tokens: int = DEFAULT_MAX_TOKENS
    reasoning_effort: str = ""
    max_rpm: int = DEFAULT_MAX_RPM
    max_tpm: int = DEFAULT_MAX_TPM

    @classmethod
    def from_config(cls, config: dict[str, str]) -> "Settings":
        provider = (config.get("REFUTE_PROVIDER") or "openai").strip().lower()
        if provider not in _DEFAULT_BASE_URL:
            raise LLMError(
                "REFUTE_PROVIDER must be 'openai' or 'anthropic', not "
                + repr(provider)
            )

        def number(key: str, fallback: int) -> int:
            raw = (config.get(key) or "").strip()
            if not raw:
                return fallback
            try:
                return int(raw)
            except ValueError:
                raise LLMError(
                    key + " must be a whole number, not " + repr(raw)
                ) from None

        base = config.get("REFUTE_BASE_URL") or _DEFAULT_BASE_URL[provider]
        return cls(
            provider=provider,
            base_url=base.rstrip("/"),
            api_key=(config.get("REFUTE_API_KEY") or "").strip(),
            model=(config.get("REFUTE_MODEL") or "").strip(),
            max_tokens=number("REFUTE_MAX_TOKENS", DEFAULT_MAX_TOKENS),
            reasoning_effort=(config.get("REFUTE_REASONING_EFFORT") or "").strip(),
            max_rpm=number("REFUTE_MAX_RPM", DEFAULT_MAX_RPM),
            max_tpm=number("REFUTE_MAX_TPM", DEFAULT_MAX_TPM),
        )


# ---------------------------------------------------------------------------
# throttle


class Throttle:
    """Sliding 60 second window over both requests and tokens.

    The clock and the sleep are injected so the tests can drive this without
    waiting. ``reserve`` blocks until the prompt estimate plus the reserved
    output tokens fit under both limits.
    """

    def __init__(
        self,
        max_rpm: int = DEFAULT_MAX_RPM,
        max_tpm: int = DEFAULT_MAX_TPM,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_rpm = max(1, max_rpm)
        self.max_tpm = max(1, max_tpm)
        self._clock = clock
        self._sleep = sleeper
        self._requests: list[float] = []
        self._tokens: list[tuple[float, int]] = []
        self.waited = 0.0

    def _prune(self, now: float) -> None:
        cutoff = now - WINDOW
        self._requests = [t for t in self._requests if t > cutoff]
        self._tokens = [(t, n) for t, n in self._tokens if t > cutoff]

    def _wait_for(self, now: float, want: int) -> float:
        """Seconds to wait before one more request of `want` tokens fits."""
        self._prune(now)
        delay = 0.0
        if len(self._requests) >= self.max_rpm:
            delay = max(delay, self._requests[0] + WINDOW - now)
        used = sum(n for _, n in self._tokens)
        if used + want > self.max_tpm and self._tokens:
            need = used + want - self.max_tpm
            freed = 0
            oldest = self._tokens[-1][0]
            for stamp, count in self._tokens:
                freed += count
                oldest = stamp
                if freed >= need:
                    break
            # If even an empty window could not fit this prompt, waiting for
            # the whole window to clear is all there is to do; the server
            # decides the rest.
            delay = max(delay, oldest + WINDOW - now)
        return max(0.0, delay)

    def reserve(self, prompt_tokens: int) -> float:
        """Block until this call fits. Returns how long it waited."""
        want = prompt_tokens + RESERVED_OUTPUT_TOKENS
        total = 0.0
        for _ in range(64):
            delay = self._wait_for(self._clock(), want)
            if delay <= 0:
                break
            self._sleep(delay)
            total += delay
        now = self._clock()
        self._requests.append(now)
        self._tokens.append((now, want))
        self.waited += total
        return total

    def record(self, actual_tokens: int) -> None:
        """Replace the last reservation with what the call really used."""
        if self._tokens:
            stamp, _ = self._tokens[-1]
            self._tokens[-1] = (stamp, max(1, actual_tokens))

    def pause(self, seconds: float) -> None:
        """Honor a server-side wait, counted into the time spent waiting."""
        if seconds > 0:
            self._sleep(seconds)
            self.waited += seconds


# ---------------------------------------------------------------------------
# cache


class Cache:
    """One JSON file per call, keyed by sha256(purpose + prompt).

    The stored value carries the model, provider and usage as well as the
    text, so a replayed run can still report where its model output came from.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def key(self, purpose: str, prompt: str) -> str:
        digest = hashlib.sha256()
        digest.update(purpose.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(prompt.encode("utf-8"))
        return digest.hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / (key + ".json")

    def get(self, purpose: str, prompt: str) -> LLMResponse | None:
        path = self._path(self.key(purpose, prompt))
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        usage = data.get("usage") or {}
        return LLMResponse(
            text=data.get("response", ""),
            purpose=data.get("purpose", purpose),
            model=data.get("model", ""),
            provider=data.get("provider", ""),
            usage=Usage(
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                estimated=bool(usage.get("estimated", False)),
            ),
            cached=True,
            finish_reason=data.get("finish_reason", "stop"),
        )

    def put(self, purpose: str, prompt: str, response: LLMResponse) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "purpose": purpose,
            "model": response.model,
            "provider": response.provider,
            "finish_reason": response.finish_reason,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "estimated": response.usage.estimated,
            },
            "response": response.text,
        }
        path = self._path(self.key(purpose, prompt))
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# client


class LLM(Protocol):
    """What the rest of Refute needs from a model. FakeLLM satisfies it too."""

    def complete(
        self, purpose: str, prompt: str, system: str = "", use_cache: bool = True
    ) -> LLMResponse: ...


class Client:
    """A throttled, retrying, cached model client."""

    def __init__(
        self,
        settings: Settings,
        cache_dir: Path | None = None,
        offline: bool = False,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.offline = offline
        self.cache = Cache(Path(cache_dir) if cache_dir else Path(".refute_cache"))
        self.throttle = Throttle(settings.max_rpm, settings.max_tpm, clock, sleeper)
        self._clock = clock
        self._transport = transport
        self._send_reasoning = bool(settings.reasoning_effort)
        self.calls = 0
        self.tokens = 0
        self.last_headers: dict[str, str] = {}

    # -- plumbing ----------------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=120.0, transport=self._transport)

    def endpoint(self) -> str:
        """The URL a call goes to. Printed by `refute ping`."""
        if self.settings.provider == "anthropic":
            return self.settings.base_url + "/v1/messages"
        return self.settings.base_url + "/chat/completions"

    def _headers(self) -> dict[str, str]:
        if self.settings.provider == "anthropic":
            return {
                "x-api-key": self.settings.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        return {
            "authorization": "Bearer " + self.settings.api_key,
            "content-type": "application/json",
        }

    def _payload(self, prompt: str, system: str) -> dict[str, object]:
        settings = self.settings
        if settings.provider == "anthropic":
            payload: dict[str, object] = {
                "model": settings.model,
                "max_tokens": settings.max_tokens,
                "temperature": TEMPERATURE,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                payload["system"] = system
        else:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            payload = {
                "model": settings.model,
                "max_tokens": settings.max_tokens,
                "temperature": TEMPERATURE,
                "messages": messages,
            }
        if self._send_reasoning and settings.reasoning_effort:
            payload["reasoning_effort"] = settings.reasoning_effort
        return payload

    @staticmethod
    def _parse(provider: str, data: dict) -> tuple[str, str, Usage]:
        """Pull text, finish reason and usage out of either response shape."""
        usage_raw = data.get("usage") or {}
        if provider == "anthropic":
            parts = [
                block.get("text", "")
                for block in data.get("content") or []
                if block.get("type") == "text"
            ]
            text = "".join(parts)
            finish = data.get("stop_reason") or "stop"
            if finish == "max_tokens":
                finish = "length"
            usage = Usage(
                prompt_tokens=int(usage_raw.get("input_tokens", 0)),
                completion_tokens=int(usage_raw.get("output_tokens", 0)),
            )
        else:
            choices = data.get("choices") or [{}]
            message = choices[0].get("message") or {}
            text = message.get("content") or ""
            finish = choices[0].get("finish_reason") or "stop"
            usage = Usage(
                prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
                completion_tokens=int(usage_raw.get("completion_tokens", 0)),
            )
        if usage.total_tokens == 0:
            usage = Usage(
                prompt_tokens=0,
                completion_tokens=estimate_tokens(text),
                estimated=True,
            )
        return text, finish, usage

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(30.0, 2.0 * (2**attempt))

    @classmethod
    def _retry_after(cls, response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("retry-after", "")
        try:
            return max(0.0, float(raw))
        except ValueError:
            return cls._backoff(attempt)

    @staticmethod
    def rate_limit_headers(headers) -> dict[str, str]:
        """The x-ratelimit-* response headers, for `refute ping` to show."""
        return {
            key.lower(): value
            for key, value in headers.items()
            if key.lower().startswith("x-ratelimit")
        }

    # -- the one public method --------------------------------------------

    def complete(
        self, purpose: str, prompt: str, system: str = "", use_cache: bool = True
    ) -> LLMResponse:
        """Answer `prompt`, from the cache if possible, otherwise from the model.

        ``use_cache=False`` forces a real call, which is what `refute ping`
        wants: a ping answered from the cache could not tell a working key
        from an expired one. --offline still reads the cache either way,
        because there is nothing else it could do.
        """
        if purpose not in PURPOSES:
            raise LLMError("unknown purpose " + repr(purpose))

        if use_cache or self.offline:
            cached = self.cache.get(purpose, prompt)
            if cached is not None:
                return cached
        if self.offline:
            raise CacheMiss(
                "--offline was given but the "
                + purpose
                + " prompt is not in "
                + str(self.cache.directory)
                + ". Run once without --offline to record it."
            )
        if not self.settings.api_key:
            raise LLMError(
                "REFUTE_API_KEY is not set. Put it in .env or the environment."
            )
        if not self.settings.model:
            raise LLMError(
                "REFUTE_MODEL is not set. Put it in .env or the environment."
            )

        response = self._request(purpose, prompt, system)
        if use_cache:
            self.cache.put(purpose, prompt, response)
        return response

    def _request(self, purpose: str, prompt: str, system: str) -> LLMResponse:
        errors: list[str] = []
        for attempt in range(MAX_ATTEMPTS):
            label = "attempt " + str(attempt + 1) + ": "
            self.throttle.reserve(estimate_tokens(prompt + system))
            started = self._clock()
            try:
                with self._client() as client:
                    http = client.post(
                        self.endpoint(),
                        headers=self._headers(),
                        json=self._payload(prompt, system),
                    )
            except httpx.HTTPError as exc:
                errors.append(label + type(exc).__name__ + ": " + str(exc))
                self.throttle.pause(self._backoff(attempt))
                continue
            latency = self._clock() - started
            self.last_headers = self.rate_limit_headers(http.headers)

            if http.status_code == 429:
                errors.append(label + "HTTP 429, rate limited")
                self.throttle.pause(self._retry_after(http, attempt))
                continue
            if http.status_code >= 500:
                errors.append(label + "HTTP " + str(http.status_code))
                self.throttle.pause(self._backoff(attempt))
                continue
            if http.status_code == 400 and self._send_reasoning:
                # This endpoint does not take reasoning_effort. Drop it for
                # the rest of the run and retry, rather than failing.
                self._send_reasoning = False
                errors.append(label + "HTTP 400, retried without reasoning_effort")
                continue
            if http.status_code != 200:
                raise LLMError(
                    "HTTP "
                    + str(http.status_code)
                    + " from "
                    + self.settings.provider
                    + ": "
                    + http.text[:300]
                )

            try:
                data = http.json()
            except ValueError:
                errors.append(label + "the response was not JSON")
                continue
            text, finish, usage = self._parse(self.settings.provider, data)
            self.throttle.record(usage.total_tokens or estimate_tokens(text))
            if finish == "length":
                # A truncated reply is unusable: a half-written script does
                # not parse. Count it as a failed attempt and try again.
                errors.append(
                    label
                    + "the reply hit REFUTE_MAX_TOKENS ("
                    + str(self.settings.max_tokens)
                    + ") and was cut off"
                )
                continue
            if not text.strip():
                errors.append(label + "the reply was empty")
                continue

            self.calls += 1
            self.tokens += usage.total_tokens
            return LLMResponse(
                text=text,
                purpose=purpose,
                model=data.get("model") or self.settings.model,
                provider=self.settings.provider,
                usage=usage,
                finish_reason=finish,
                latency=latency,
                headers=dict(self.last_headers),
            )

        raise LLMError(
            "the "
            + purpose
            + " call failed after "
            + str(MAX_ATTEMPTS)
            + " attempts: "
            + "; ".join(errors)
        )


class FakeLLM:
    """A model stand-in for the tests. Answers by purpose, never over a socket.

    Execution is never mocked anywhere in Refute. Only this is.
    """

    def __init__(
        self, answers: dict[str, object] | None = None, model: str = "fake-model"
    ) -> None:
        self.answers = dict(answers or {})
        self.model = model
        self.prompts: list[tuple[str, str]] = []
        self.calls = 0
        self.tokens = 0

    def set(self, purpose: str, answer: object) -> None:
        self.answers[purpose] = answer

    def complete(
        self, purpose: str, prompt: str, system: str = "", use_cache: bool = True
    ) -> LLMResponse:
        if purpose not in PURPOSES:
            raise LLMError("unknown purpose " + repr(purpose))
        self.prompts.append((purpose, prompt))
        if purpose not in self.answers:
            raise LLMError("FakeLLM has no answer for purpose " + repr(purpose))
        answer = self.answers[purpose]
        if callable(answer):
            # A callable answer lets a test reply differently to a repair.
            answer = answer(prompt)
        if isinstance(answer, list):
            # A list is consumed one reply per call, for repair rounds.
            answer = answer.pop(0) if answer else ""
        text = str(answer)
        usage = Usage(
            prompt_tokens=estimate_tokens(prompt),
            completion_tokens=estimate_tokens(text),
            estimated=True,
        )
        self.calls += 1
        self.tokens += usage.total_tokens
        return LLMResponse(
            text=text,
            purpose=purpose,
            model=self.model,
            provider="fake",
            usage=usage,
        )


def build_client(
    cache_dir: Path | None = None,
    offline: bool = False,
    env_path: Path | None = None,
) -> Client:
    """The client the CLI uses: configuration from .env plus the environment."""
    return Client(
        Settings.from_config(load_config(env_path)),
        cache_dir=cache_dir,
        offline=offline,
    )

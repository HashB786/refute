"""`refute ping` prints the configuration and never prints the key."""

from __future__ import annotations

from pathlib import Path

import httpx
from rich.console import Console

from refute.cli import build_parser, main, ping
from refute.llm import Client, Settings
from refute.models import Limits, Verdict, out_paths


def client_for(tmp_path, handler, **overrides):
    resolved = Settings(
        provider=overrides.pop("provider", "openai"),
        base_url="https://example.invalid/v1",
        api_key=overrides.pop("api_key", "secret-key"),
        model="test-model",
        **overrides,
    )
    return Client(
        resolved, cache_dir=tmp_path, transport=httpx.MockTransport(handler)
    )


def test_ping_reports_provider_model_latency_tokens_and_headers(tmp_path, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [
                    {
                        "message": {"content": "refute ping ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 9, "completion_tokens": 4},
            },
            headers={"x-ratelimit-remaining-requests": "19"},
        )

    code = ping(client_for(tmp_path, handler), Console(width=100))
    out = capsys.readouterr().out
    assert code == 0
    assert "openai" in out
    assert "test-model" in out
    assert "latency" in out
    assert "9 prompt, 4 completion, reported" in out
    assert "x-ratelimit-remaining-requests" in out
    assert "secret-key" not in out


def test_ping_says_so_when_no_rate_limit_headers_were_sent(tmp_path, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )

    assert ping(client_for(tmp_path, handler), Console(width=100)) == 0
    assert "no x-ratelimit-* headers" in capsys.readouterr().out


def test_ping_reports_a_failure_without_a_traceback(tmp_path, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    code = ping(client_for(tmp_path, handler), Console(width=100))
    out = capsys.readouterr().out
    assert code == 1
    assert "failed" in out
    assert "401" in out


def test_ping_offline_on_a_miss_is_an_error_not_a_crash(tmp_path, capsys):
    client = Client(
        Settings(provider="openai", base_url="x", api_key="", model="m"),
        cache_dir=tmp_path,
        offline=True,
    )
    assert ping(client, Console(width=100)) == 1
    assert "offline" in capsys.readouterr().out


def test_main_prints_help_and_fails_when_no_command_is_given(capsys):
    assert main([]) == 1
    assert "usage" in capsys.readouterr().out.lower()


def test_the_later_phase_commands_are_declared_but_not_implemented(capsys):
    assert main(["demo"]) == 1
    assert "later phase" in capsys.readouterr().out


def test_the_parser_accepts_the_documented_run_options():
    args = build_parser().parse_args(
        [
            "run",
            "statement.md",
            "sol.py",
            "--oracle",
            "ref.py",
            "--samples",
            "samples",
            "--no-ai-explain",
            "--budget",
            "50",
            "--offline",
        ]
    )
    assert args.command == "run"
    assert args.solution.name == "sol.py"
    assert args.oracle.name == "ref.py"
    assert args.no_ai_explain
    assert args.budget == 50
    assert args.offline


def test_the_models_shared_by_every_phase_are_consistent():
    assert Verdict.WA.is_failure() and not Verdict.PASS.is_failure()
    assert Verdict.INVALID.is_skipped() and not Verdict.WA.is_skipped()
    assert Limits().target < Limits().oracle
    paths = out_paths(Path("refute-out"))
    assert {path.name for path in paths.values()} == {
        "report.html",
        "report.json",
        "counterexample.in",
        "expected.out",
        "actual.out",
        "shrink.json",
    }
    assert all(path.parent == Path("refute-out") for path in paths.values())

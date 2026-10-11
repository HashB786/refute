"""The `refute` command.

Phase 1A provides `refute ping`, which makes one real model call and reports
what came back: provider, model, latency, token usage and any rate-limit
headers the endpoint sent. It is the check that configuration works before a
run spends tokens on tooling.

`refute run`, `refute demo` and `refute bench` arrive in later phases.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .llm import CacheMiss, Client, LLMError, Settings, build_client

PING_PROMPT = "Reply with exactly: refute ping ok"
NOT_YET = "This command arrives in a later phase."


def _table() -> Table:
    """A borderless two-column table, the one console shape Refute uses."""
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim", no_wrap=True)
    table.add_column(overflow="fold")
    return table


def ping(client: Client, console: Console) -> int:
    """Make one model call and print what the endpoint said about it."""
    console.print("Calling " + client.settings.provider + " ...", style="dim")
    try:
        # Not cached unless --offline: a ping answered from disk could not
        # tell a working key from an expired one.
        response = client.complete("samples", PING_PROMPT, use_cache=False)
    except CacheMiss as exc:
        console.print("offline: " + str(exc))
        return 1
    except LLMError as exc:
        console.print("ping failed: " + str(exc))
        return 1

    usage = response.usage
    table = _table()
    table.add_row("provider", response.provider or client.settings.provider)
    table.add_row("endpoint", client._endpoint())
    table.add_row("model", response.model or client.settings.model)
    if response.cached:
        table.add_row("source", "cache (no call was made)")
    else:
        table.add_row("latency", format(response.latency, ".2f") + " s")
    counted = "estimated" if usage.estimated else "reported"
    table.add_row(
        "tokens",
        str(usage.prompt_tokens)
        + " prompt, "
        + str(usage.completion_tokens)
        + " completion, "
        + counted,
    )
    table.add_row("reply", response.text.strip().splitlines()[0][:80] if response.text.strip() else "(empty)")
    console.print(table)

    if response.headers:
        console.print("")
        limits = _table()
        for key in sorted(response.headers):
            limits.add_row(key, response.headers[key])
        console.print(limits)
    elif not response.cached:
        console.print("", style="dim")
        console.print("no x-ratelimit-* headers were sent", style="dim")
    return 0


def _settings_line(settings: Settings) -> str:
    """A one-line description of the configuration, with no key in it."""
    key = "set" if settings.api_key else "missing"
    return (
        settings.provider
        + ", model "
        + (settings.model or "unset")
        + ", key "
        + key
        + ", "
        + str(settings.max_rpm)
        + " rpm / "
        + str(settings.max_tpm)
        + " tpm"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refute",
        description=(
            "Find the smallest input that proves a program wrong. "
            "Refute runs untrusted code; the Dockerfile with --network none "
            "is the recommended way to run it."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    ping_parser = sub.add_parser("ping", help="check the model configuration")
    ping_parser.add_argument("--cache-dir", type=Path, default=None)
    ping_parser.add_argument(
        "--offline", action="store_true", help="use the cache only, never the network"
    )

    run_parser = sub.add_parser("run", help="run the pipeline on a target (phase 1B)")
    run_parser.add_argument("statement", type=Path)
    run_parser.add_argument("solution", type=Path)
    run_parser.add_argument("--oracle", type=Path, default=None)
    run_parser.add_argument("--samples", type=Path, default=None)
    run_parser.add_argument("--float-tol", type=float, default=None)
    run_parser.add_argument("--no-ai-explain", action="store_true")
    run_parser.add_argument("--budget", type=int, default=None)
    run_parser.add_argument("--cache-dir", type=Path, default=None)
    run_parser.add_argument("--offline", action="store_true")

    sub.add_parser("demo", help="replay the recorded example offline (phase 2)")
    bench_parser = sub.add_parser("bench", help="run the benchmark (phase 3)")
    bench_parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()

    if args.command == "ping":
        try:
            client = build_client(cache_dir=args.cache_dir, offline=args.offline)
        except LLMError as exc:
            console.print("configuration error: " + str(exc))
            return 1
        console.print(_settings_line(client.settings), style="dim")
        return ping(client, console)

    if args.command in ("run", "demo", "bench"):
        console.print(args.command + ": " + NOT_YET)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

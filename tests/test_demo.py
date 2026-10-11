"""`refute demo`: the recording it replays, and what it says it is doing.

The run itself is the manual check in README.md, because it takes about a
hundred seconds of real subprocesses. What is checked here is that the
recording is complete and committed, that the demo asks for no network, and
that it says clearly which part was recorded.
"""

from __future__ import annotations

import json

from refute import cli
from refute.llm import Cache
from refute.models import PURPOSES

RECORDED = ("oracle_a", "oracle_b", "validator", "generator", "diagnose", "fix")


def test_the_example_and_its_recording_are_in_the_repository():
    assert (cli.DEMO_PROBLEM / "statement.md").is_file()
    assert (cli.DEMO_PROBLEM / "greedy.py").is_file()
    assert (cli.DEMO_PROBLEM / "samples" / "1.in").is_file()
    assert cli.DEMO_CACHE.is_dir()


def test_the_recording_covers_every_purpose_a_full_run_asks_for():
    """It has to answer at least the demo's calls; holding more is fine.

    A recording made without --samples also carries a samples answer, so
    this covers what the demo needs rather than matching it exactly.
    """
    found = {}
    for path in cli.DEMO_CACHE.glob("*.json"):
        entry = json.loads(path.read_text(encoding="utf-8"))
        found[entry["purpose"]] = entry
    assert set(RECORDED) <= set(found)
    for purpose, entry in sorted(found.items()):
        assert purpose in PURPOSES, purpose
        assert entry["response"].strip(), purpose
        assert entry["model"], purpose
        assert entry["provider"], purpose


def test_the_recording_holds_no_key():
    for path in cli.DEMO_CACHE.glob("*.json"):
        text = path.read_text(encoding="utf-8").lower()
        assert "api_key" not in text
        assert "authorization" not in text
        assert "bearer" not in text


def test_each_recorded_file_is_named_after_its_prompt():
    # The key is sha256(purpose + prompt), so a file whose name does not match
    # its own contents would never be found again.
    cache = Cache(cli.DEMO_CACHE)
    for path in cli.DEMO_CACHE.glob("*.json"):
        entry = json.loads(path.read_text(encoding="utf-8"))
        assert len(path.stem) == 64
        assert path.stem == path.stem.lower()
        assert cache.get(entry["purpose"], "not the recorded prompt") is None


def test_the_demo_runs_the_example_offline_against_the_recording(monkeypatch):
    seen = {}

    def capture(args, console):
        seen.update(vars(args))
        return 0

    monkeypatch.setattr(cli, "run", capture)
    assert cli.demo(cli.make_console()) == 0
    assert seen["offline"] is True
    assert seen["cache_dir"] == cli.DEMO_CACHE
    assert seen["statement"] == cli.DEMO_PROBLEM / "statement.md"
    assert seen["solution"] == cli.DEMO_PROBLEM / "greedy.py"
    assert seen["samples"] == cli.DEMO_PROBLEM / "samples"
    # The demo shows the whole report, so it does not skip the explanation.
    assert seen["no_ai_explain"] is False
    assert seen["oracle"] is None


def test_the_demo_says_what_was_recorded_and_what_was_run(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run", lambda args, console: 0)
    cli.demo(cli.make_console())
    out = " ".join(capsys.readouterr().out.split())
    assert "replaying recorded model output" in out
    assert "every program here is really run" in out


def test_the_demo_banner_fits_the_console(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run", lambda args, console: 0)
    cli.demo(cli.make_console())
    for line in capsys.readouterr().out.splitlines():
        assert len(line) <= cli.WIDTH


def test_a_missing_recording_is_reported_plainly(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "DEMO_CACHE", tmp_path / "nothing")
    assert cli.demo(cli.make_console()) == 1
    assert "no recorded model output" in capsys.readouterr().out


def test_a_missing_example_is_reported_plainly(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "DEMO_PROBLEM", tmp_path / "gone")
    assert cli.demo(cli.make_console()) == 1
    assert "the example is missing" in capsys.readouterr().out


def test_main_routes_the_demo_command(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "demo", lambda console: called.append(1) or 7)
    assert cli.main(["demo"]) == 7
    assert called == [1]


def test_a_recorded_answer_is_found_again_from_the_statement_it_was_made_for():
    """The key is a hash of the prompt, so the recording is tied to the text.

    Editing the statement or the samples invalidates the recording, and the
    demo would then report a cache miss instead of replaying stale answers.
    """
    from refute.llm import Cache
    from refute.synth import extract_samples

    statement = (cli.DEMO_PROBLEM / "statement.md").read_text(encoding="utf-8")
    cache = Cache(cli.DEMO_CACHE)

    class Replay:
        def complete(self, purpose, prompt, system="", use_cache=True):
            hit = cache.get(purpose, prompt)
            assert hit is not None, purpose + " is not recorded for this statement"
            return hit

    kept, dropped = extract_samples(Replay(), statement)
    assert dropped == 0
    assert [sample.input_text for sample in kept] == ["3 11\n1 5 10\n", "4 7\n1 2 3 5\n"]
    assert all(sample.source == "model_extracted" for sample in kept)

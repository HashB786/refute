"""Tooling is only kept if running it says so.

The model is a FakeLLM here. Everything else is real: real subprocesses, real
validators, real generators, real failures fed back as repair prompts.
"""

from __future__ import annotations

import pytest

from refute.llm import FakeLLM
from refute.models import Limits, Sample
from refute.sandbox import workspace
from refute.synth import (
    Calibration,
    Smoke,
    SynthError,
    build_script,
    extract_code,
    extract_samples,
    generator_check,
    keep_verbatim,
    load_samples,
    mutations,
    normalize,
    oracle_check,
    parse_sample_reply,
    synthesize,
    validator_check,
)

STATEMENT = """
# Sum a list

The first line holds one integer n. The second line holds n integers.
Print their sum.

Constraints: 1 <= n <= 30 and 0 <= each value <= 100.

## Examples

Input:

```
3
1 2 3
```

Output:

```
6
```

Input:

```
1
7
```

Output:

```
7
```
"""

SAMPLES = [
    Sample("1", "3\n1 2 3\n", "6\n", "samples_dir"),
    Sample("2", "1\n7\n", "7\n", "samples_dir"),
]

GOOD_ORACLE = """
import sys
data = sys.stdin.read().split()
n = int(data[0])
print(sum(int(x) for x in data[1 : 1 + n]))
"""

GOOD_VALIDATOR = """
import sys
data = sys.stdin.read().split()
if len(data) < 1:
    sys.exit(1)
try:
    values = [int(x) for x in data]
except ValueError:
    sys.exit(1)
n = values[0]
if not 1 <= n <= 30:
    sys.exit(1)
if len(values) != n + 1:
    sys.exit(1)
for value in values[1:]:
    if not 0 <= value <= 100:
        sys.exit(1)
sys.exit(0)
"""

GOOD_GENERATOR = """
import random
import sys
seed = int(sys.argv[1])
size = int(sys.argv[2])
mode = sys.argv[3]
rng = random.Random(seed)
n = max(1, min(30, size))
if mode == "tiny_values":
    top = 2
elif mode == "extreme":
    top = 100
else:
    top = 50
values = [rng.randint(0, top) for _ in range(n)]
print(n)
print(" ".join(str(v) for v in values))
"""


def fenced(code: str) -> str:
    return "Here you go.\n\n```python\n" + code.strip() + "\n```\n"


# ---------------------------------------------------------------------------
# the verbatim sample filter


def test_a_sample_that_is_not_in_the_statement_is_discarded():
    candidates = [
        Sample("1", "3\n1 2 3\n", "6\n", "model_extracted"),
        Sample("2", "4\n9 9 9 9\n", "36\n", "model_extracted"),
    ]
    kept = keep_verbatim(STATEMENT, candidates)
    assert [sample.name for sample in kept] == ["1"]


def test_whitespace_differences_do_not_disqualify_a_sample():
    # The statement's layout is not part of the data, so only the tokens count.
    candidates = [Sample("1", "3\n   1  2   3  \n", "  6 \n", "model_extracted")]
    assert len(keep_verbatim(STATEMENT, candidates)) == 1


def test_a_sample_with_the_right_input_but_an_invented_output_is_discarded():
    candidates = [Sample("1", "3\n1 2 3\n", "99999\n", "model_extracted")]
    assert keep_verbatim(STATEMENT, candidates) == []


def test_an_empty_sample_is_discarded():
    assert keep_verbatim(STATEMENT, [Sample("1", "  \n", "6\n", "model_extracted")]) == []


def test_extract_samples_counts_what_it_threw_away():
    reply = (
        '```json\n[{"input": "3\\n1 2 3", "output": "6"},'
        ' {"input": "5\\n1 1 1 1 1", "output": "5"}]\n```'
    )
    kept, dropped = extract_samples(FakeLLM({"samples": reply}), STATEMENT)
    assert len(kept) == 1
    assert dropped == 1
    assert kept[0].source == "model_extracted"


def test_a_reply_that_is_not_json_yields_no_samples():
    assert parse_sample_reply("I could not find any examples.") == []
    assert parse_sample_reply('```json\n{"input": "x"}\n```') == []


def test_extract_code_takes_the_fenced_block():
    assert "print" in extract_code(fenced("print(1)"))
    with pytest.raises(SynthError):
        extract_code("there is no code here")


def test_normalize_flattens_whitespace():
    assert normalize("  a\n b  c ") == "a b c"


def test_load_samples_reads_numbered_pairs(tmp_path):
    (tmp_path / "1.in").write_text("3\n1 2 3\n", encoding="utf-8")
    (tmp_path / "1.out").write_text("6\n", encoding="utf-8")
    (tmp_path / "10.in").write_text("1\n7\n", encoding="utf-8")
    (tmp_path / "10.out").write_text("7\n", encoding="utf-8")
    (tmp_path / "2.in").write_text("2\n1 1\n", encoding="utf-8")  # no .out, skipped
    samples = load_samples(tmp_path)
    assert [sample.name for sample in samples] == ["1", "10"]
    assert samples[0].source == "samples_dir"

    with pytest.raises(SynthError):
        load_samples(tmp_path / "nothing")


# ---------------------------------------------------------------------------
# the validator calibration mutations


def test_the_three_mutations_break_the_input_in_three_ways():
    broken = dict(mutations("3\n1 2 3\n"))
    assert broken["the last token removed"] == "3\n1 2\n"
    assert broken["an extra token appended"] == "3\n1 2 3 1\n"
    assert broken["the first number out of range"] == "1000000000000000000\n1 2 3\n"


def test_a_mutation_identical_to_the_original_is_not_offered():
    # A single token has no "first number" to push that is not also the token
    # that gets removed, so only distinct inputs come back.
    for _, text in mutations("5\n"):
        assert text.strip() != "5"


def test_mutations_of_a_one_line_input_still_produce_three():
    assert len(mutations("1 2 3\n")) == 3


def test_a_strict_validator_passes_calibration():
    calibration = Calibration()
    with workspace() as directory:
        check = validator_check(SAMPLES, Limits(), calibration)
        from refute.sandbox import write_script

        path = write_script(directory, "validator.py", GOOD_VALIDATOR)
        assert check(path) == ""
    assert calibration.mutations == 6
    assert calibration.rejected == 6
    assert not calibration.weak
    assert "6 of 6 rejected" in calibration.summary


def test_a_validator_that_accepts_everything_is_reported_as_blind():
    calibration = Calibration()
    with workspace() as directory:
        from refute.sandbox import write_script

        path = write_script(directory, "validator.py", "import sys\nsys.exit(0)\n")
        problem = validator_check(SAMPLES, Limits(), calibration)(path)
    assert "accepted every broken copy" in problem
    assert calibration.rejected == 0


def test_a_validator_that_rejects_a_sample_is_refused():
    calibration = Calibration()
    with workspace() as directory:
        from refute.sandbox import write_script

        path = write_script(directory, "validator.py", "import sys\nsys.exit(1)\n")
        problem = validator_check(SAMPLES, Limits(), calibration)(path)
    assert "it rejected example 1" in problem


def test_a_weak_validator_lets_the_run_continue_but_is_marked(tmp_path):
    llm = FakeLLM(
        {
            "oracle_a": fenced(GOOD_ORACLE),
            "oracle_b": fenced(GOOD_ORACLE),
            "validator": fenced("import sys\nsys.exit(0)\n"),
            "generator": fenced(GOOD_GENERATOR),
        }
    )
    with workspace() as directory:
        tooling = synthesize(llm, STATEMENT, directory, samples=SAMPLES)
    assert tooling.calibration.weak
    assert "weak" in tooling.validator.notes
    assert any("weak validator" in note for note in tooling.notes)


# ---------------------------------------------------------------------------
# the oracle check


def test_an_oracle_that_misses_a_sample_is_refused():
    with workspace() as directory:
        from refute.sandbox import write_script

        path = write_script(directory, "oracle_a.py", "print(0)\n")
        problem = oracle_check(SAMPLES, Limits(), None)(path)
    assert "printed '0'" in problem
    assert "the statement says '6'" in problem


def test_an_oracle_that_crashes_is_refused():
    with workspace() as directory:
        from refute.sandbox import write_script

        path = write_script(directory, "oracle_a.py", "raise SystemExit(2)\n")
        problem = oracle_check(SAMPLES, Limits(), None)(path)
    assert "exited with status 2" in problem


def test_a_float_tolerance_is_honored_by_the_oracle_check():
    samples = [Sample("1", "x\n", "1.0\n", "samples_dir")]
    with workspace() as directory:
        from refute.sandbox import write_script

        path = write_script(directory, "oracle_a.py", "print(1.0001)\n")
        assert oracle_check(samples, Limits(), None)(path) != ""
        assert oracle_check(samples, Limits(), 1e-3)(path) == ""


# ---------------------------------------------------------------------------
# the generator smoke set


def test_the_generator_smoke_set_runs_every_size_mode_and_seed():
    smoke = Smoke()
    with workspace() as directory:
        from refute.sandbox import write_script

        validator = write_script(directory, "validator.py", GOOD_VALIDATOR)
        generator = write_script(directory, "gen.py", GOOD_GENERATOR)
        assert generator_check(validator, Limits(), smoke)(generator) == ""
    assert smoke.runs == 5 * 3 * 5
    assert smoke.accepted == smoke.runs
    assert smoke.distinct >= smoke.runs // 3


def test_a_generator_the_validator_rejects_is_refused():
    smoke = Smoke()
    bad = """
import sys
print(99)
print("1 2 3")
"""
    with workspace() as directory:
        from refute.sandbox import write_script

        validator = write_script(directory, "validator.py", GOOD_VALIDATOR)
        generator = write_script(directory, "gen.py", bad)
        problem = generator_check(validator, Limits(), smoke)(generator)
    assert "the validator rejects" in problem


def test_an_unseeded_generator_is_refused():
    unseeded = """
import random
import sys
n = random.Random().randint(1, 30)
print(n)
print(" ".join("1" for _ in range(n)))
"""
    smoke = Smoke()
    with workspace() as directory:
        from refute.sandbox import write_script

        validator = write_script(directory, "validator.py", GOOD_VALIDATOR)
        generator = write_script(directory, "gen.py", unseeded)
        problem = generator_check(validator, Limits(), smoke)(generator)
    assert "run twice" in problem
    assert "random.Random(SEED)" in problem


def test_a_generator_that_ignores_its_arguments_is_refused_as_not_varied():
    fixed = """
import sys
print(1)
print(0)
"""
    smoke = Smoke()
    with workspace() as directory:
        from refute.sandbox import write_script

        validator = write_script(directory, "validator.py", GOOD_VALIDATOR)
        generator = write_script(directory, "gen.py", fixed)
        problem = generator_check(validator, Limits(), smoke)(generator)
    assert "only 1 different inputs" in problem
    assert smoke.distinct == 1


# ---------------------------------------------------------------------------
# the repair loop


def test_a_script_is_repaired_with_the_concrete_failure_fed_back():
    replies = [fenced("import sys\nsys.exit(1)\n"), fenced(GOOD_ORACLE)]
    llm = FakeLLM({"oracle_a": replies})
    with workspace() as directory:
        script = build_script(
            llm,
            "oracle_a",
            "write a solution",
            "oracle_a.py",
            directory,
            oracle_check(SAMPLES, Limits(), None),
        )
    assert script.repairs == 1
    assert llm.calls == 2
    # The second prompt carried both the broken script and the real reason.
    second = llm.prompts[1][1]
    assert "exited with status 1" in second
    assert "sys.exit(1)" in second


def test_a_script_the_guard_refuses_is_sent_back_with_the_reason():
    replies = [fenced("import os\nprint(os.getcwd())\n"), fenced(GOOD_ORACLE)]
    llm = FakeLLM({"oracle_a": replies})
    with workspace() as directory:
        script = build_script(
            llm,
            "oracle_a",
            "write a solution",
            "oracle_a.py",
            directory,
            oracle_check(SAMPLES, Limits(), None),
        )
    assert script.repairs == 1
    assert "the guard refused it" in llm.prompts[1][1]
    assert "'os'" in llm.prompts[1][1]


def test_a_reply_with_no_code_block_is_sent_back():
    llm = FakeLLM({"oracle_a": ["I am not sure.", fenced(GOOD_ORACLE)]})
    with workspace() as directory:
        script = build_script(
            llm,
            "oracle_a",
            "write a solution",
            "oracle_a.py",
            directory,
            oracle_check(SAMPLES, Limits(), None),
        )
    assert script.repairs == 1


def test_a_script_gives_up_after_three_repairs():
    llm = FakeLLM({"oracle_a": [fenced("print(0)")] * 8})
    with workspace() as directory:
        with pytest.raises(SynthError) as caught:
            build_script(
                llm,
                "oracle_a",
                "write a solution",
                "oracle_a.py",
                directory,
                oracle_check(SAMPLES, Limits(), None),
            )
    assert "after 3 repairs" in str(caught.value)
    assert llm.calls == 4


# ---------------------------------------------------------------------------
# the whole of synth


def test_synthesize_produces_four_checked_scripts():
    llm = FakeLLM(
        {
            "oracle_a": fenced(GOOD_ORACLE),
            "oracle_b": fenced(GOOD_ORACLE),
            "validator": fenced(GOOD_VALIDATOR),
            "generator": fenced(GOOD_GENERATOR),
        }
    )
    steps: list[str] = []
    with workspace() as directory:
        tooling = synthesize(
            llm, STATEMENT, directory, samples=SAMPLES, on_step=steps.append
        )
        assert set(tooling.paths) == {"oracle_a", "oracle_b", "validator", "generator"}
        assert all(path.is_file() for path in tooling.paths.values())
    assert tooling.repairs == 0
    assert not tooling.calibration.weak
    assert tooling.smoke.accepted == 75
    assert steps == [
        "writing reference A",
        "writing reference B",
        "writing the validator",
        "writing the generator",
    ]


def test_a_trusted_oracle_skips_the_model_written_ones(tmp_path):
    reference = tmp_path / "ref.py"
    reference.write_text(GOOD_ORACLE, encoding="utf-8")
    llm = FakeLLM(
        {"validator": fenced(GOOD_VALIDATOR), "generator": fenced(GOOD_GENERATOR)}
    )
    with workspace() as directory:
        tooling = synthesize(
            llm, STATEMENT, directory, samples=SAMPLES, oracle=reference
        )
    assert tooling.trusted_oracle
    assert tooling.oracle_b is None
    assert [purpose for purpose, _ in llm.prompts] == ["validator", "generator"]


def test_a_trusted_oracle_that_misses_the_samples_stops_the_run(tmp_path):
    reference = tmp_path / "ref.py"
    reference.write_text("print(0)\n", encoding="utf-8")
    llm = FakeLLM({})
    with workspace() as directory:
        with pytest.raises(SynthError) as caught:
            synthesize(llm, STATEMENT, directory, samples=SAMPLES, oracle=reference)
    assert "--oracle does not match the examples" in str(caught.value)


def test_synthesize_refuses_to_work_without_examples():
    llm = FakeLLM({"samples": "```json\n[]\n```"})
    with workspace() as directory:
        with pytest.raises(SynthError) as caught:
            synthesize(llm, STATEMENT, directory)
    assert "no usable examples" in str(caught.value)


def test_the_tooling_prompts_never_mention_the_target():
    # Tooling is cached per problem, so it must not depend on what is tested.
    llm = FakeLLM(
        {
            "oracle_a": fenced(GOOD_ORACLE),
            "oracle_b": fenced(GOOD_ORACLE),
            "validator": fenced(GOOD_VALIDATOR),
            "generator": fenced(GOOD_GENERATOR),
        }
    )
    with workspace() as directory:
        synthesize(llm, STATEMENT, directory, samples=SAMPLES)
    for _, prompt in llm.prompts:
        assert "target" not in prompt.lower()
        assert "greedy" not in prompt.lower()


SEED_BLIND_IN_ONE_MODE = """
import random
import sys
seed = int(sys.argv[1])
size = int(sys.argv[2])
mode = sys.argv[3]
rng = random.Random(seed)
n = max(1, min(30, size))
if mode == "tiny_values":
    # The seed is ignored here: this mode is the same input every time.
    values = [i % 3 for i in range(n)]
else:
    values = [rng.randint(0, 100) for _ in range(n)]
print(n)
print(" ".join(str(v) for v in values))
"""


def test_a_generator_that_ignores_its_seed_in_one_mode_is_refused():
    # The stress run uses eight new seeds per size and mode. Where the seed
    # does not change the input, those eight tests are one test run eight
    # times. This generator varies with the seed in two modes out of three, so
    # the overall variety looks fine and only the per-mode check catches it.
    smoke = Smoke()
    with workspace() as directory:
        from refute.sandbox import write_script

        validator = write_script(directory, "validator.py", GOOD_VALIDATOR)
        generator = write_script(directory, "gen.py", SEED_BLIND_IN_ONE_MODE)
        problem = generator_check(validator, Limits(), smoke)(generator)
    assert "MODE tiny_values every seed printed exactly the same input" in problem
    assert "SEED has to decide what is in it" in problem
    # It got past the overall variety check, which is the point of this one.
    assert smoke.distinct >= max(2, smoke.runs // 3)


def test_the_generator_prompt_says_the_seed_must_change_the_input():
    from refute.synth import generator_prompt

    prompt = generator_prompt(STATEMENT, SAMPLES)
    assert "SEED decides what is in it" in prompt
    assert "Do not work a value out from SIZE alone" in prompt

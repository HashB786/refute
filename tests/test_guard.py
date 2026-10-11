"""The guard must refuse the ways a model-written script could leave the box."""

from __future__ import annotations

import pytest

from refute.guard import ALLOWED_IMPORTS, GuardError, check, describe, is_safe

GOOD = """
import sys
import math
from collections import Counter
from itertools import permutations

data = sys.stdin.read().split()
n = int(data[0])
counts = Counter(data[1:])
print(math.gcd(n, 12), len(counts), len(list(permutations(range(3)))))
"""


def test_accepts_an_ordinary_brute_force():
    check(GOOD)
    assert is_safe(GOOD)
    assert describe(GOOD) == ""


@pytest.mark.parametrize("module", sorted(ALLOWED_IMPORTS))
def test_every_allowed_import_is_accepted(module):
    check("import " + module)
    check("from " + module + " import *")


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "import subprocess",
        "import socket",
        "import shutil",
        "import os.path",
        "from os import system",
        "from pathlib import Path",
        "import sys, os",
        "from . import helper",
    ],
)
def test_rejects_imports_outside_the_allowed_set(source):
    assert not is_safe(source)
    with pytest.raises(GuardError):
        check(source)


@pytest.mark.parametrize(
    "source",
    [
        "open('/etc/passwd')",
        "exec('x = 1')",
        "eval('1 + 1')",
        "compile('1', 'f', 'eval')",
        "__import__('os')",
        "globals()['x'] = 1",
        "locals()",
        "vars(object())",
        "f = open",
        "breakpoint()",
    ],
)
def test_rejects_the_banned_builtins(source):
    assert not is_safe(source)


@pytest.mark.parametrize(
    "source",
    [
        "().__class__.__bases__",
        "x = (1).__class__",
        "print(object.__subclasses__())",
        "getattr(object, 'x')",
        "setattr(object, 'x', 1)",
        "delattr(object, 'x')",
    ],
)
def test_rejects_dunder_access_and_dynamic_attributes(source):
    assert not is_safe(source)


def test_dunder_names_the_attribute_in_the_message():
    message = describe("().__class__")
    assert "__class__" in message
    assert "line 1" in message


def test_the_main_guard_a_model_always_writes_is_accepted():
    # The rule is dunder *attribute* access. A bare __name__ reaches nothing,
    # and refusing it would spend a repair round on almost every script.
    assert is_safe("if __name__ == '__main__':\n    main()")
    assert not is_safe("x = value.__name__")


def test_syntax_error_is_a_guard_error_with_a_line():
    with pytest.raises(GuardError) as caught:
        check("def broken(:\n    pass")
    assert "syntax error" in str(caught.value)


def test_several_problems_are_all_reported():
    message = describe("import os\nopen('x')\n")
    assert "os" in message
    assert "open" in message

"""Static check applied to model-written scripts and model-written fixes.

This is not a security boundary. It is a cheap filter that catches a model
that wandered off into reading files or shelling out, so the obvious mistakes
fail before anything runs. The sandbox, and the Dockerfile with
``--network none``, are what actually contain untrusted code.
"""

from __future__ import annotations

import ast

#: Modules a model-written script may import. Everything a brute force,
#: a validator or a generator needs, and nothing that touches the outside.
ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        "sys",
        "math",
        "random",
        "itertools",
        "collections",
        "heapq",
        "bisect",
        "functools",
        "fractions",
        "decimal",
        "string",
        "re",
        "array",
    }
)

#: Names that give a script a way out of the process, so they are refused
#: whether they are called, assigned or merely referenced.
BANNED_NAMES: frozenset[str] = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "globals",
        "locals",
        "vars",
        "input",
        "breakpoint",
        "memoryview",
    }
)


class GuardError(Exception):
    """A model-written script was refused. The message names line and reason."""


def _root(name: str) -> str:
    return name.split(".", 1)[0]


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.problems: list[str] = []

    def _bad(self, node: ast.AST, reason: str) -> None:
        line = getattr(node, "lineno", 0)
        self.problems.append(f"line {line}: {reason}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = _root(alias.name)
            if root not in ALLOWED_IMPORTS:
                self._bad(node, f"import of {alias.name!r} is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self._bad(node, "relative import is not allowed")
        elif node.module is None or _root(node.module) not in ALLOWED_IMPORTS:
            self._bad(node, f"import from {node.module!r} is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BANNED_NAMES:
            self._bad(node, f"use of {node.id!r} is not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self._bad(node, f"dunder attribute {node.attr!r} is not allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # getattr(x, "__class__") sidesteps visit_Attribute, so refuse the
        # dynamic-attribute builtins outright.
        func = node.func
        if isinstance(func, ast.Name) and func.id in ("getattr", "setattr", "delattr"):
            self._bad(node, f"use of {func.id!r} is not allowed")
        self.generic_visit(node)


def check(source: str) -> None:
    """Raise GuardError if the source is unparseable or breaks a rule."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise GuardError(f"line {exc.lineno or 0}: syntax error: {exc.msg}") from exc
    visitor = _Visitor()
    visitor.visit(tree)
    if visitor.problems:
        raise GuardError("; ".join(visitor.problems))


def is_safe(source: str) -> bool:
    """check() as a boolean, for callers that only want to branch."""
    try:
        check(source)
    except GuardError:
        return False
    return True


def describe(source: str) -> str:
    """The guard's complaint as a single line, or an empty string if it passed."""
    try:
        check(source)
    except GuardError as exc:
        return str(exc)
    return ""

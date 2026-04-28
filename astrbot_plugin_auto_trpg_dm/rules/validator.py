from __future__ import annotations

import ast


class RuleValidationError(ValueError):
    pass


class RuleValidator(ast.NodeVisitor):
    """Validate a small Python subset for pure TRPG calculations.

    This is not a strong security boundary. It is a pragmatic MVP guardrail
    for local, trusted deployments; the runtime is intentionally replaceable.
    """

    FORBIDDEN_NODES = (
        ast.Import,
        ast.ImportFrom,
        ast.ClassDef,
        ast.Global,
        ast.Nonlocal,
        ast.Lambda,
        ast.With,
        ast.AsyncWith,
        ast.AsyncFunctionDef,
        ast.Await,
        ast.Yield,
        ast.YieldFrom,
        ast.Try,
        ast.Raise,
        ast.Delete,
    )
    FORBIDDEN_NAMES = {
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "input",
        "print",
        "breakpoint",
        "help",
        "exit",
        "quit",
    }

    def validate(self, code: str) -> ast.Module:
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            raise RuleValidationError(f"syntax error: {exc}") from exc
        self.visit(tree)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        if len(functions) != 1 or functions[0].name != "calculate":
            raise RuleValidationError("rule must define exactly one function named calculate")
        return tree

    def visit(self, node: ast.AST):  # type: ignore[override]
        if isinstance(node, self.FORBIDDEN_NODES):
            raise RuleValidationError(f"forbidden syntax: {node.__class__.__name__}")
        return super().visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name != "calculate":
            raise RuleValidationError("only calculate function is allowed")
        if node.decorator_list:
            raise RuleValidationError("decorators are not allowed")
        if node.returns is not None:
            self.visit(node.returns)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__") or node.id in self.FORBIDDEN_NAMES:
            raise RuleValidationError(f"forbidden name: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__"):
            raise RuleValidationError("dunder attribute access is forbidden")
        if node.attr != "get":
            raise RuleValidationError("attribute access is forbidden in rule code")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in self.FORBIDDEN_NAMES:
            raise RuleValidationError(f"forbidden call: {node.func.id}")
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            self.generic_visit(node)
            return
        if not isinstance(node.func, ast.Name):
            raise RuleValidationError("only direct calls to safe functions are allowed")
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        raise RuleValidationError("while loops are forbidden; use bounded for loops")

    def visit_For(self, node: ast.For) -> None:
        self.generic_visit(node)

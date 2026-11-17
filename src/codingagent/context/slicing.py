from __future__ import annotations

import ast
from dataclasses import dataclass, field


class SliceError(RuntimeError):
    pass


@dataclass
class SliceResult:
    symbol: str
    found: bool
    code: str = ""
    symbols: list[str] = field(default_factory=list)
    span: tuple[int, int] | None = None
    top_level: list[str] = field(default_factory=list)
    error: str | None = None


def parse_module(source: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise SliceError(f"源码存在语法错误: {exc.msg} (line {exc.lineno})") from exc


def module_docstring(source: str) -> str:
    """QuixBugs 的说明文字常写在文件末尾的独立字符串里，两种形式都要支持。"""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    doc = (ast.get_docstring(tree) or "").strip()
    if doc:
        return doc
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                return node.value.value.strip()
    return ""


def top_level_symbols(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]


def _definition_map(tree: ast.Module) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _loaded_names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _module_assignments(tree: ast.Module) -> dict[str, ast.stmt]:
    assignments: dict[str, ast.stmt] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assignments[node.target.id] = node
    return assignments


def _import_segments(tree: ast.Module, source: str) -> list[str]:
    segments = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            segment = ast.get_source_segment(source, node)
            if segment:
                segments.append(segment)
    return segments


def _segment(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node, padded=True)
    if segment is None:
        raise SliceError("无法从源码中提取 AST 片段")
    return segment.strip("\n")


def function_source(source: str, symbol: str) -> str | None:
    """只取目标函数/类本身（用于结构化回喂的 relevant_code）。"""

    try:
        tree = parse_module(source)
    except SliceError:
        return None
    node = _definition_map(tree).get(symbol)
    return _segment(source, node) if node else None


def slice_function(source: str, symbol: str, max_depth: int = 2) -> SliceResult:
    """定位目标函数，递归收集同文件内被调用的依赖，拼成最小可读片段。

    这是 S1 相对 S0 的全部差异：去掉无关函数与噪声，只留目标及其依赖。
    """

    try:
        tree = parse_module(source)
    except SliceError as exc:
        return SliceResult(symbol=symbol, found=False, error=str(exc), top_level=top_level_symbols(source))

    definitions = _definition_map(tree)
    top_level = list(definitions)
    target = definitions.get(symbol)
    if target is None:
        return SliceResult(symbol=symbol, found=False, top_level=top_level)

    included: dict[str, ast.AST] = {symbol: target}
    frontier = [target]
    for _ in range(max_depth):
        next_frontier: list[ast.AST] = []
        for node in frontier:
            for name in _called_names(node):
                if name in definitions and name not in included:
                    included[name] = definitions[name]
                    next_frontier.append(definitions[name])
        if not next_frontier:
            break
        frontier = next_frontier

    referenced = set()
    for node in included.values():
        referenced |= _loaded_names(node)
    assignments = _module_assignments(tree)
    for name, node in assignments.items():
        if name in referenced:
            included.setdefault(f"__assign__{name}", node)

    ordered = sorted(included.values(), key=lambda node: getattr(node, "lineno", 0))
    body_parts = [_segment(source, node) for node in ordered]
    code = "\n\n\n".join([*_import_segments(tree, source), *body_parts])

    span: tuple[int, int] | None = None
    if ordered:
        span = (
            min(getattr(node, "lineno", 0) for node in ordered),
            max(getattr(node, "end_lineno", 0) or 0 for node in ordered),
        )

    names = [name for name in included if not name.startswith("__assign__")]
    return SliceResult(
        symbol=symbol,
        found=True,
        code=code,
        symbols=names,
        span=span,
        top_level=top_level,
    )


def function_arity(source: str, symbol: str) -> int | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    node = _definition_map(tree).get(symbol)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return len(node.args.args)
    return None


def identifier_tokens(source: str) -> set[str]:
    """提取标识符词袋，用于 few-shot 相似度（不引入 embedding 依赖）。"""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr.lower())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tokens.add(node.name.lower())
    return tokens


def doc_tokens(text: str) -> set[str]:
    import re

    return {word.lower() for word in re.findall(r"[A-Za-z_]{3,}", text)}

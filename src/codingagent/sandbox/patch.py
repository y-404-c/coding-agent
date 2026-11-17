from __future__ import annotations

import re
from typing import Mapping

from .workspace import FilePatch

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(RuntimeError):
    """patch 无法应用到目标文件（上下文不匹配）。"""


def _strip_prefix(path: str) -> str:
    path = path.strip()
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def _apply_hunks(source: str, hunks: list[tuple[tuple[str, ...], list[str]]]) -> str:
    src_lines = source.splitlines()
    out: list[str] = []
    cursor = 0
    for groups, body in hunks:
        old_start = int(groups[0])
        old_count = int(groups[1]) if groups[1] else 1
        start = old_start - 1 if old_count > 0 else old_start
        if start < cursor or start > len(src_lines):
            raise PatchError(f"hunk 位置越界: @@ -{old_start} @@（当前游标 {cursor}）")
        out.extend(src_lines[cursor:start])
        cursor = start
        for raw in body:
            if raw == "":
                tag, text = " ", ""
            else:
                tag, text = raw[0], raw[1:]
            if tag == " ":
                if cursor >= len(src_lines) or src_lines[cursor] != text:
                    raise PatchError(f"上下文不匹配: 期望 {text!r}")
                out.append(src_lines[cursor])
                cursor += 1
            elif tag == "-":
                if cursor >= len(src_lines) or src_lines[cursor] != text:
                    raise PatchError(f"删除行不匹配: 期望 {text!r}")
                cursor += 1
            elif tag == "+":
                out.append(text)
            elif tag == "\\":
                continue
            else:
                raise PatchError(f"无法识别的 diff 行: {raw!r}")
    out.extend(src_lines[cursor:])
    return "\n".join(out) + ("\n" if source.endswith("\n") else "")


def apply_unified_diff(patch: str, files: Mapping[str, str]) -> dict[str, str]:
    """把 unified diff 应用到原始文件集合，返回新文件集合。

    用于评测端独立复算：即使 Agent 在内存里改，评测仍以 patch 为准重新落盘。
    """

    result = {k: v for k, v in files.items()}
    lines = patch.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue
        if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
            raise PatchError("patch 头部不完整：缺少 +++ 行")
        path = _strip_prefix(lines[index + 1][4:])
        index += 2
        hunks: list[tuple[tuple[str, ...], list[str]]] = []
        while index < len(lines) and lines[index].startswith("@@"):
            match = _HUNK_HEADER.match(lines[index])
            if not match:
                raise PatchError(f"无法解析 hunk 头: {lines[index]!r}")
            index += 1
            body: list[str] = []
            while index < len(lines) and (
                lines[index].startswith((" ", "+", "-")) or lines[index] == ""
            ):
                body.append(lines[index])
                index += 1
            hunks.append((match.groups(), body))
        if path not in result:
            raise PatchError(f"patch 指向不存在的文件: {path}")
        result[path] = _apply_hunks(result[path], hunks)
    return result


def render_patches(patches: list[FilePatch]) -> str:
    """把内存工作区的改动渲染成 unified diff（不落盘）。"""

    import difflib

    chunks = []
    for patch in patches:
        chunks.append(
            "".join(
                difflib.unified_diff(
                    patch.before.splitlines(keepends=True),
                    patch.after.splitlines(keepends=True),
                    fromfile=f"a/{patch.path}",
                    tofile=f"b/{patch.path}",
                    n=3,
                )
            )
        )
    return "".join(chunks)

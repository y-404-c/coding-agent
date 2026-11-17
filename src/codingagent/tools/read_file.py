from __future__ import annotations

from typing import Any, Mapping

from codingagent.domain.models import ToolResult

from .base import BaseTool, ToolContext

MAX_LINES = 220


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "读取源码文件（返回带行号的全文；行号不属于内容，edit_file 的 old 不要带行号）。"
    arg_schema = {"path": "文件路径，可省略则读入口文件"}

    def _run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolResult:
        path = str(args.get("path") or ctx.problem.entry_path)
        resolved = ctx.workspace.resolve(path)
        if resolved is None:
            return self._fail(
                f"文件不存在: {path}。当前可用文件: {sorted(ctx.workspace.paths)}"
            )
        content = ctx.workspace.read(resolved)
        lines = content.splitlines()
        truncated = len(lines) > MAX_LINES
        shown = lines[:MAX_LINES]
        body = "\n".join(f"{index:>4} | {line}" for index, line in enumerate(shown, start=1))
        if truncated:
            body += f"\n...（文件共 {len(lines)} 行，已截断）"
        return ToolResult(
            ok=True,
            output=f"`{resolved}`（{len(lines)} 行）:\n{body}",
            data={
                "path": resolved,
                "target_hit": resolved == ctx.problem.entry_path,
                "line_count": len(lines),
            },
        )

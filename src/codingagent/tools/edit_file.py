from __future__ import annotations

from typing import Any, Mapping

from codingagent.domain.models import ToolResult

from .base import BaseTool, ToolContext

MAX_DIFF_CHARS = 4000


class EditFileTool(BaseTool):
    name = "edit_file"
    description = (
        "生成一处精确替换（old 必须是当前文件中唯一出现的原文，new 是替换后的文本）。"
        "只写内存 patch，不直接落盘，可回滚。"
    )
    arg_schema = {"old": "原文片段（唯一、缩进完全一致）", "new": "替换后的片段", "path": "可选，文件路径"}

    def _run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolResult:
        old = str(args.get("old") or "")
        new = str(args.get("new") or "")
        path = str(args.get("path") or ctx.problem.entry_path)

        if not old:
            return self._fail("old 不能为空：请从当前文件复制要替换的那段原文。")

        outcome = ctx.workspace.apply_edit(path, old, new)
        if not outcome.ok:
            return self._fail(outcome.message)

        diff = outcome.diff
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + "\n...（diff 已截断）"
        return ToolResult(
            ok=True,
            output=f"{outcome.message}\n```diff\n{diff}```",
            data={
                "path": ctx.workspace.resolve(path),
                "target_hit": ctx.workspace.resolve(path) == ctx.problem.entry_path,
            },
        )

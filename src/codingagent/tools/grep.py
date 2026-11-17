from __future__ import annotations

import re
from typing import Any, Mapping

from codingagent.domain.models import ToolResult

from .base import BaseTool, ToolContext

MAX_MATCHES = 40


class GrepTool(BaseTool):
    name = "grep"
    description = "在工作区文件里做正则/字面量搜索，返回 文件:行号: 内容。"
    arg_schema = {"pattern": "要搜索的正则或关键词"}

    def _run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolResult:
        pattern = str(args.get("pattern") or "").strip()
        if not pattern:
            return self._fail("pattern 不能为空。传一个正则或关键词，例如 `def gcd`。")

        try:
            regex = re.compile(pattern)
            mode = "regex"
        except re.error:
            regex = re.compile(re.escape(pattern))
            mode = "literal"

        matches: list[str] = []
        hit_entry = False
        for path in ctx.workspace.paths:
            for lineno, line in enumerate(ctx.workspace.read(path).splitlines(), start=1):
                if regex.search(line):
                    matches.append(f"{path}:{lineno}: {line.strip()}")
                    hit_entry = hit_entry or path == ctx.problem.entry_path
                    if len(matches) >= MAX_MATCHES:
                        break
            if len(matches) >= MAX_MATCHES:
                break

        if not matches:
            return ToolResult(
                ok=False,
                output=(
                    f"没有匹配 `{pattern}`（{mode}）。尝试更短的关键词或函数名，"
                    "或先用 read_file / ast_slice 看清结构。"
                ),
            )
        header = f"匹配 {len(matches)} 条（{mode}）："
        return ToolResult(
            ok=True,
            output=header + "\n" + "\n".join(matches),
            data={"matches": len(matches), "target_hit": hit_entry},
        )

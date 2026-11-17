from __future__ import annotations

from typing import Any, Mapping

from codingagent.context.slicing import slice_function
from codingagent.domain.enums import ErrorCategory
from codingagent.domain.models import ToolResult

from .base import BaseTool, ToolContext


class AstSliceTool(BaseTool):
    name = "ast_slice"
    description = "按符号名提取目标函数 + 同文件直接依赖（减少无关代码噪声）。"
    arg_schema = {"symbol": "要提取的函数/类名", "path": "可选，文件路径"}

    def _run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolResult:
        symbol = str(args.get("symbol") or "").strip()
        path = str(args.get("path") or ctx.problem.entry_path)
        resolved = ctx.workspace.resolve(path)
        if resolved is None:
            return self._fail(f"文件不存在: {path}。可用文件: {sorted(ctx.workspace.paths)}")
        if not symbol:
            symbol = ctx.problem.function_name

        result = slice_function(ctx.workspace.read(resolved), symbol)
        if result.error:
            return self._fail(f"解析 {resolved} 失败: {result.error}", ErrorCategory.SYNTAX_ERROR)
        if not result.found:
            return self._fail(
                f"符号 `{symbol}` 不存在。文件顶层符号: {result.top_level}"
            )

        header = (
            f"`{resolved}` 中符号 `{result.symbol}` 的切片"
            f"（含依赖 {result.symbols}，原始行 {result.span[0]}-{result.span[1]}）："
        )
        return ToolResult(
            ok=True,
            output=f"{header}\n```python\n{result.code}\n```",
            data={
                "symbol": result.symbol,
                "symbols": result.symbols,
                "span": result.span,
                "target_hit": result.symbol == ctx.problem.function_name,
            },
        )

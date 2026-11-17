from __future__ import annotations

from collections import OrderedDict

from codingagent.context.base import BuildContext, ContextBuilder
from codingagent.context.fewshot import FewShotPool
from codingagent.context.slicing import slice_function
from codingagent.domain.enums import Strategy


class FullFileContextBuilder(ContextBuilder):
    """S0：整文件塞入，朴素做法的上限。"""

    strategy = Strategy.S0

    def sections(self, ctx: BuildContext) -> OrderedDict[str, str]:
        source = self._source(ctx)
        return OrderedDict(
            [("完整源文件", self._fence(source, ctx.problem.entry_path))]
        )


class AstSliceContextBuilder(ContextBuilder):
    """S1：只给目标函数 + 同文件直接依赖。"""

    strategy = Strategy.S1

    def sections(self, ctx: BuildContext) -> OrderedDict[str, str]:
        source = self._source(ctx)
        result = slice_function(source, ctx.problem.function_name)
        if not result.found:
            fallback = (
                f"未能定位符号 `{ctx.problem.function_name}`，已回退为整文件。"
                f"文件顶层符号: {result.top_level}"
            )
            return OrderedDict(
                [
                    ("AST 切片（失败回退）", fallback),
                    ("完整源文件", self._fence(source, ctx.problem.entry_path)),
                ]
            )
        header = (
            f"目标符号 `{result.symbol}` 及其同文件依赖 "
            f"{result.symbols}（原始行 {result.span[0]}-{result.span[1]}）"
        )
        return OrderedDict(
            [
                ("AST 切片", f"{header}\n```python\n{result.code}\n```"),
            ]
        )

    def meta(self, ctx: BuildContext) -> dict:
        result = slice_function(self._source(ctx), ctx.problem.function_name)
        return {
            "strategy": self.strategy.value,
            "slice_found": result.found,
            "slice_symbols": result.symbols,
            "slice_lines": result.span,
        }


class AstSliceWithTestsContextBuilder(AstSliceContextBuilder):
    """S2：S1 + 官方测试（正确性信号）。"""

    strategy = Strategy.S2

    def sections(self, ctx: BuildContext) -> OrderedDict[str, str]:
        sections = super().sections(ctx)
        sections["官方测试（判定标准）"] = self._fence(
            ctx.problem.test_code, ctx.problem.test_path
        )
        return sections


class FewShotContextBuilder(AstSliceWithTestsContextBuilder):
    """S3：S2 + 2-3 个相似题的官方修复范例。"""

    strategy = Strategy.S3

    def __init__(self, pool: FewShotPool) -> None:
        self._pool = pool
        self._rendered: dict[str, str] = {}

    def sections(self, ctx: BuildContext) -> OrderedDict[str, str]:
        sections = super().sections(ctx)
        examples = self._pool.for_problem(ctx.problem)
        sections["few-shot 修复范例"] = self._render(examples)
        self._last_examples = examples
        return sections

    def _render(self, examples: list[dict[str, object]]) -> str:
        from codingagent.context.fewshot import FewShotSelector

        return FewShotSelector.render(examples)

    def meta(self, ctx: BuildContext) -> dict:
        meta = super().meta(ctx)
        meta["fewshot_ids"] = [
            example["problem_id"] for example in getattr(self, "_last_examples", [])
        ]
        return meta


class CorrectiveContextBuilder(FewShotContextBuilder):
    """S4：与 S3 同内容；差异在失败后的**结构化**回喂（见 correction 层）。

    pass@1 列上 S3 与 S4 必须完全一致，这正是"只改回喂形态"的对照点。
    """

    strategy = Strategy.S4

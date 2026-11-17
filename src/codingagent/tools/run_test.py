from __future__ import annotations

from typing import Any, Mapping

from codingagent.domain.models import ToolResult

from .base import BaseTool, ToolContext


class RunTestTool(BaseTool):
    name = "run_test"
    description = "在沙箱里跑该题的官方测试，返回通过/失败、失败用例、断言期望与实际。"
    arg_schema = {"test_id": "可选，QuixBugs 每题只有一个官方测试文件，可省略"}

    def _run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolResult:
        test_id = str(args.get("test_id") or ctx.problem.problem_id)
        if test_id not in {ctx.problem.problem_id, ctx.problem.test_path}:
            return self._fail(
                f"未知测试 `{test_id}`。可用测试: ['{ctx.problem.problem_id}']"
            )

        report = ctx.grader.grade_workspace(ctx.workspace)
        from codingagent.correction.classifier import classify_test_report, render_report

        classification = classify_test_report(report)
        output = render_report(report, classification)
        return ToolResult(
            ok=report.ok,
            output=output,
            error_category=None if report.ok else classification.category,
            data={
                "passed": report.passed,
                "failed": report.failed,
                "errors": report.errors,
                "failed_tests": report.failed_tests,
                "error_types": report.error_types,
                "duration_ms": report.duration_ms,
                "target_hit": False,
            },
        )

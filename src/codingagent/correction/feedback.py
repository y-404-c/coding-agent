from __future__ import annotations

import json
import re

from codingagent.context.slicing import function_source
from codingagent.domain.enums import ErrorCategory
from codingagent.domain.models import Classification, Problem, TestReport
from codingagent.sandbox.workspace import Workspace


class FeedbackBuilder:
    """回喂不是裸 traceback：要告诉模型错在哪类、期望/实际、相关代码、允许改的范围。"""

    def build(
        self,
        style: str,
        *,
        problem: Problem,
        workspace: Workspace,
        report: TestReport,
        classification: Classification,
        last_patch: str,
    ) -> str | None:
        if style == "none":
            return None
        if style == "structured":
            return self.build_structured(
                problem=problem,
                workspace=workspace,
                classification=classification,
                last_patch=last_patch,
            )
        return self.build_raw(report=report, classification=classification, last_patch=last_patch)

    def build_structured(
        self,
        *,
        problem: Problem,
        workspace: Workspace,
        classification: Classification,
        last_patch: str,
    ) -> str:
        payload = {
            "error_category": classification.category.value,
            "failed_test": classification.failed_test,
            "expected": classification.expected,
            "actual": classification.actual,
            "exception_type": classification.exception_type,
            "relevant_code": function_source(
                workspace.read(problem.entry_path), problem.function_name
            ),
            "related_schema_hint": self._schema_hint(problem.description),
            "last_patch": last_patch or "（上一次没有产生 patch）",
            "instruction": (
                f"只修改 `{problem.function_name}` 内部实现，不要改动其他函数或文件；"
                "引用 relevant_code 做最小改动，再用 run_test 验证。"
            ),
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        return (
            "上一次尝试失败，下面是**结构化**回喂（不是原始 traceback）：\n"
            f"```json\n{body}\n```\n"
            "请针对 error_category 与 expected/actual 的差异给出新的最小 patch。"
        )

    def build_raw(
        self,
        *,
        report: TestReport,
        classification: Classification,
        last_patch: str,
    ) -> str:
        tail = report.output_tail[-2000:]
        return (
            "上一次尝试失败。测试输出（原始文本，未加工）：\n"
            f"```\n{tail}\n```\n"
            f"上次 patch:\n```diff\n{last_patch or '（无改动）'}\n```\n"
            "请重新修改后再跑测试。"
        )

    @staticmethod
    def _schema_hint(description: str) -> str:
        match = re.search(
            r"Output:\s*\n?(.*?)(?:\n\s*\n|Examples:|$)", description or "", re.DOTALL
        )
        hint = " ".join(match.group(1).split()) if match else ""
        pieces = []
        if hint:
            pieces.append(f"输出语义: {hint}")
        pieces.append("注意边界条件与输入类型")
        return "；".join(pieces)


def is_retryable(classification: Classification) -> bool:
    return classification.category.retryable and classification.category != ErrorCategory.NONE

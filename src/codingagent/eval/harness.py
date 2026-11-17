from __future__ import annotations

from codingagent.config import Settings
from codingagent.dataset import QuixBugsDataset
from codingagent.domain.models import RunTrace, TestReport
from codingagent.sandbox.grading import PatchGrader
from codingagent.sandbox.runner import TestRunner


class EvaluationHarness:
    """独立判定器：只认测试结果。可对任意历史 trace 的 patch 重新复跑。"""

    def __init__(self, dataset: QuixBugsDataset, settings: Settings) -> None:
        self.dataset = dataset
        self.settings = settings
        self._support_files = dataset.support_files()

    def _runner(self, problem_id: str) -> TestRunner:
        problem = self.dataset.load(problem_id)
        return TestRunner(problem, self._support_files, self.settings.sandbox)

    def regrade_patch(self, problem_id: str, patch_text: str) -> TestReport:
        problem = self.dataset.load(problem_id)
        grader = PatchGrader(problem, self._runner(problem_id))
        return grader.grade_patch(patch_text)

    def regrade_trace(self, trace: RunTrace) -> tuple[TestReport, bool]:
        """返回（重新跑出的测试报告，是否通过）。patch 为空视为未通过。"""

        patch = trace.final_patch
        if not patch.strip():
            return TestReport(returncode=-1, output_tail="patch 为空"), False
        report = self.regrade_patch(trace.problem_id, patch)
        return report, report.ok

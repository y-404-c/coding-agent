from __future__ import annotations

from typing import Mapping

from codingagent.domain.models import Problem, TestReport

from .patch import apply_unified_diff
from .runner import TestRunner
from .workspace import Workspace


class PatchGrader:
    """评测端独立复算：把 patch 应用到原始副本，再跑官方测试。"""

    def __init__(self, problem: Problem, runner: TestRunner) -> None:
        self.problem = problem
        self.runner = runner

    def grade_files(self, files: Mapping[str, str]) -> TestReport:
        return self.runner.run(files)

    def grade_workspace(self, workspace: Workspace) -> TestReport:
        return self.grade_files(workspace.files)

    def grade_patch(self, patch_text: str) -> TestReport:
        patched = apply_unified_diff(patch_text, self.runner.pristine_files)
        return self.runner.run(patched)


def build_runner(
    problem: Problem,
    support_files: Mapping[str, str],
    settings,
) -> TestRunner:
    return TestRunner(problem, support_files, settings)

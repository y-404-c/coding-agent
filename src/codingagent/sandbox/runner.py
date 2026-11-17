from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Mapping

from codingagent.config import SandboxSettings
from codingagent.domain.models import Problem, TestReport

# 官方 conftest 只提供 --correct / --runslow 两个开关，测试文件会访问 pytest.use_correct。
_SANDBOX_CONFTEST = '''import pytest


def pytest_addoption(parser):
    parser.addoption("--correct", action="store_true", default=False)
    parser.addoption("--runslow", action="store_true", default=False)


def pytest_configure(config):
    pytest.use_correct = bool(config.getoption("--correct"))
    pytest.run_slow = bool(config.getoption("--runslow"))
'''

_COUNT_PATTERNS = (
    ("passed", re.compile(r"(\d+) passed")),
    ("failed", re.compile(r"(\d+) failed")),
    ("errors", re.compile(r"(\d+) error(?:s)?\b")),
    ("skipped", re.compile(r"(\d+) skipped")),
)


def parse_pytest_output(
    output: str,
    *,
    returncode: int,
    duration_ms: int,
    timed_out: bool,
    command: list[str],
    max_chars: int = 8000,
) -> TestReport:
    counts = {name: 0 for name, _ in _COUNT_PATTERNS}
    for name, pattern in _COUNT_PATTERNS:
        match = pattern.search(output)
        if match:
            counts[name] = int(match.group(1))

    failed_tests = _dedupe(re.findall(r"^FAILED (\S+)", output, flags=re.MULTILINE))
    failed_tests += _dedupe(re.findall(r"^ERROR (\S+)", output, flags=re.MULTILINE))

    error_types = _dedupe(
        re.findall(r"^E\s+([A-Za-z_.]*(?:Error|Exception))\b", output, flags=re.MULTILINE)
    )
    error_types += _dedupe(re.findall(r"^FAILED .* - ([A-Za-z_.]*(?:Error|Exception))", output, flags=re.MULTILINE))

    assertion_lines = _dedupe(
        [line.strip() for line in re.findall(r"^E\s+(assert .*)$", output, flags=re.MULTILINE)]
    )

    return TestReport(
        returncode=returncode,
        timed_out=timed_out,
        duration_ms=duration_ms,
        passed=counts["passed"],
        failed=counts["failed"],
        errors=counts["errors"],
        skipped=counts["skipped"],
        failed_tests=failed_tests[:20],
        error_types=error_types[:10],
        assertion_lines=assertion_lines[:10],
        output_tail=output[-max_chars:],
        command=command,
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


class TestRunner:
    """在临时沙箱里跑官方测试。判定物是测试结果，不是 patch 文本。"""

    __test__ = False

    def __init__(
        self,
        problem: Problem,
        support_files: Mapping[str, str],
        settings: SandboxSettings,
        *,
        correct_program: str | None = None,
    ) -> None:
        self.problem = problem
        self.support_files = dict(support_files)
        self.settings = settings
        self.correct_program = correct_program
        self.pristine_files: dict[str, str] = {problem.entry_path: problem.buggy_code}

    def run(
        self,
        files: Mapping[str, str],
        *,
        timeout_s: float | None = None,
        use_correct: bool = False,
    ) -> TestReport:
        timeout = timeout_s if timeout_s is not None else self.settings.timeout_s
        started = time.perf_counter()
        command: list[str] = []
        with tempfile.TemporaryDirectory(prefix="ca-sandbox-") as tmp:
            root = Path(tmp)
            self._materialize(root, files, use_correct)
            test_target = self.problem.test_path
            command = [
                self.settings.python_executable,
                "-m",
                "pytest",
                test_target,
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
            ]
            if use_correct:
                command.append("--correct")
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join(
                [str(root)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
            )
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
                output = (completed.stdout or "") + (completed.stderr or "")
                duration_ms = int((time.perf_counter() - started) * 1000)
                return parse_pytest_output(
                    output,
                    returncode=completed.returncode,
                    duration_ms=duration_ms,
                    timed_out=False,
                    command=command,
                    max_chars=self.settings.max_output_chars,
                )
            except subprocess.TimeoutExpired as exc:
                output = _decode(exc.stdout) + _decode(exc.stderr)
                return parse_pytest_output(
                    output,
                    returncode=-1,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    timed_out=True,
                    command=command,
                    max_chars=self.settings.max_output_chars,
                )
            except FileNotFoundError as exc:
                return TestReport(
                    returncode=-1,
                    output_tail=f"无法启动 Python 解释器: {exc}",
                    command=command,
                )

    def _materialize(self, root: Path, files: Mapping[str, str], use_correct: bool) -> None:
        programs_dir = root / "python_programs"
        tests_dir = root / "python_testcases"
        programs_dir.mkdir(parents=True, exist_ok=True)
        tests_dir.mkdir(parents=True, exist_ok=True)
        (root / "json_testcases").mkdir(parents=True, exist_ok=True)

        (root / "conftest.py").write_text(_SANDBOX_CONFTEST, encoding="utf-8")
        (programs_dir / "__init__.py").write_text("", encoding="utf-8")

        for path, content in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        for name, content in self.support_files.items():
            (tests_dir / name).write_text(content, encoding="utf-8")

        (root / self.problem.test_path).write_text(self.problem.test_code, encoding="utf-8")

        if self.problem.test_json is not None:
            json_path = root / "json_testcases" / f"{self.problem.problem_id}.json"
            json_path.write_text(self.problem.test_json, encoding="utf-8")

        if use_correct:
            correct_dir = root / "correct_python_programs"
            correct_dir.mkdir(parents=True, exist_ok=True)
            (correct_dir / "__init__.py").write_text("", encoding="utf-8")
            content = self.correct_program if self.correct_program is not None else self.problem.correct_code
            (correct_dir / f"{self.problem.problem_id}.py").write_text(content, encoding="utf-8")


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value

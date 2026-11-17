from __future__ import annotations

import re
import sys

from codingagent.domain.enums import ErrorCategory
from codingagent.domain.models import Classification, TestReport
from codingagent.sandbox.workspace import Workspace

# “不可纠”在这里硬编码：超时/OOM/依赖环境/不明类别，重试只会再烧钱
_LIBRARY_MODULES = {"pytest", "numpy", "pandas", "hypothesis"}


def classify_test_report(report: TestReport) -> Classification:
    text = report.output_tail
    failed_test = report.failed_tests[0] if report.failed_tests else _first_failed_test(text)
    excerpt = text[-1500:]
    if report.ok:
        return Classification(category=ErrorCategory.NONE, excerpt="")
    if report.timed_out:
        return Classification(
            category=ErrorCategory.TIMEOUT,
            retryable=False,
            failed_test=failed_test,
            excerpt="测试执行超时（沙箱已杀死进程），不重试。",
        )

    exception_type = _first_exception(report, text)

    if "MemoryError" in text:
        return _result(ErrorCategory.MEMORY_ERROR, failed_test, exception_type, excerpt)

    if re.search(r"ModuleNotFoundError|ImportError: No module named", text):
        module = _missing_module(text)
        if module:
            root = module.split(".")[0]
            if root in sys.stdlib_module_names or root in _LIBRARY_MODULES:
                # 环境缺依赖，不是这道题代码的问题
                return _result(ErrorCategory.ENV_ERROR, failed_test, exception_type, excerpt)
            if root in {"python_programs", "correct_python_programs", "load_testdata"}:
                return _result(ErrorCategory.IMPORT_ERROR, failed_test, exception_type, excerpt)
        return _result(ErrorCategory.IMPORT_ERROR, failed_test, exception_type, excerpt)

    if re.search(r"\b(SyntaxError|IndentationError|TabError)\b", text):
        return _result(ErrorCategory.SYNTAX_ERROR, failed_test, exception_type, excerpt)

    if re.search(r"\b(NameError|UnboundLocalError)\b", text):
        return _result(ErrorCategory.NAME_ERROR, failed_test, exception_type, excerpt)

    assertion_line = report.assertion_lines[0] if report.assertion_lines else _first_assertion(text)
    if assertion_line or re.search(r"\bAssertionError\b", text):
        actual, expected = _parse_assertion(assertion_line or "")
        return Classification(
            category=ErrorCategory.ASSERTION_FAILURE,
            retryable=True,
            failed_test=failed_test,
            expected=expected,
            actual=actual,
            exception_type="AssertionError",
            excerpt=excerpt,
        )

    if exception_type and exception_type not in {"AssertionError"}:
        return _result(ErrorCategory.RUNTIME_ERROR, failed_test, exception_type, excerpt)

    return _result(ErrorCategory.UNKNOWN, failed_test, exception_type, excerpt)


def classify_patch_state(workspace: Workspace) -> Classification | None:
    """测试通过与否之外的“无效修复”甄别。"""

    if not workspace.changed():
        return Classification(
            category=ErrorCategory.EMPTY_PATCH,
            retryable=True,
            excerpt="本次尝试没有产生任何 patch。",
        )
    if not workspace.substantive_change():
        return Classification(
            category=ErrorCategory.NOOP_PATCH,
            retryable=True,
            excerpt="改动只涉及空白/空行，未触达逻辑。",
        )
    return None


def render_report(report: TestReport, classification: Classification) -> str:
    """工具返回值：给模型看的结构化摘要（不是裸 traceback）。"""

    lines = [
        "测试结果: PASS" if report.ok else "测试结果: FAIL",
        f"通过 {report.passed} / 失败 {report.failed} / 错误 {report.errors}"
        f"（{report.duration_ms} ms）",
    ]
    if report.timed_out:
        lines.append("状态: 超时（不可重试类别）")
    if report.failed_tests:
        lines.append("失败用例: " + ", ".join(report.failed_tests[:5]))
    if report.error_types:
        lines.append("异常类型: " + ", ".join(report.error_types[:5]))
    if classification.expected or classification.actual:
        lines.append(f"期望: {classification.expected} | 实际: {classification.actual}")
    if not report.ok:
        lines.append(f"归因: {classification.category.value}（可重试: {classification.retryable}）")
        lines.append("输出尾部:\n```\n" + report.output_tail[-1200:] + "\n```")
    return "\n".join(lines)


def _first_failed_test(text: str) -> str | None:
    match = re.search(r"^(?:FAILED|ERROR) (\S+)", text, re.MULTILINE)
    return match.group(1) if match else None


def _first_assertion(text: str) -> str:
    match = re.search(r"^E\s+(assert .*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _first_exception(report: TestReport, text: str) -> str | None:
    if report.error_types:
        return report.error_types[0]
    match = re.search(r"^E\s+([A-Za-z_.]*(?:Error|Exception))\b", text, re.MULTILINE)
    return match.group(1).split(".")[-1] if match else None


def _missing_module(text: str) -> str | None:
    match = re.search(r"No module named '([^']+)'", text)
    return match.group(1) if match else None


def _parse_assertion(line: str) -> tuple[str | None, str | None]:
    if not line:
        return None, None
    match = re.match(r"assert (.+?) == (.+)$", line)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    not_match = re.match(r"assert not (.+)$", line)
    if not_match:
        return "(truthy)", "False"
    truthy = re.match(r"assert (.+)$", line)
    if truthy:
        return truthy.group(1).strip(), "True"
    return None, None


def _result(
    category: ErrorCategory,
    failed_test: str | None,
    exception_type: str | None,
    excerpt: str,
) -> Classification:
    return Classification(
        category=category,
        retryable=category.retryable,
        failed_test=failed_test,
        exception_type=exception_type,
        excerpt=excerpt,
    )

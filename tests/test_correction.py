from codingagent.correction.classifier import classify_patch_state, classify_test_report
from codingagent.domain.enums import ErrorCategory, FeedbackStyle, Strategy
from codingagent.correction.policy import CorrectionPolicy
from codingagent.domain.models import Classification, TestReport


def _report(output: str, **kwargs) -> TestReport:
    base = dict(returncode=1, passed=0, failed=1, errors=0, output_tail=output)
    base.update(kwargs)
    return TestReport(**base)


def test_passing_report_is_none():
    report = TestReport(returncode=0, passed=6, failed=0)
    classification = classify_test_report(report)
    assert classification.category == ErrorCategory.NONE
    assert not classification.retryable


def test_assertion_extracts_expected_and_actual():
    output = (
        "FAILED python_testcases/test_x.py::test_x[1-2] - AssertionError: assert 1 == 0\n"
        "E       assert 1 == 0\n"
    )
    classification = classify_test_report(_report(output))
    assert classification.category == ErrorCategory.ASSERTION_FAILURE
    assert classification.retryable
    assert classification.actual == "1"
    assert classification.expected == "0"
    assert classification.failed_test and "test_x" in classification.failed_test


def test_timeout_is_not_retryable():
    report = TestReport(timed_out=True, returncode=-1, output_tail="")
    classification = classify_test_report(report)
    assert classification.category == ErrorCategory.TIMEOUT
    assert not classification.retryable


def test_syntax_error_is_retryable():
    classification = classify_test_report(_report("E   SyntaxError: invalid syntax"))
    assert classification.category == ErrorCategory.SYNTAX_ERROR
    assert classification.retryable


def test_name_error_is_retryable():
    classification = classify_test_report(_report("E   NameError: name 'x' is not defined"))
    assert classification.category == ErrorCategory.NAME_ERROR
    assert classification.retryable


def test_missing_third_party_dependency_is_env_error():
    output = "E   ModuleNotFoundError: No module named 'numpy'"
    classification = classify_test_report(_report(output))
    assert classification.category == ErrorCategory.ENV_ERROR
    assert not classification.retryable


def test_missing_project_module_is_import_error():
    output = "E   ModuleNotFoundError: No module named 'python_programs.foo'"
    classification = classify_test_report(_report(output))
    assert classification.category == ErrorCategory.IMPORT_ERROR
    assert classification.retryable


def test_memory_error_is_not_retryable():
    classification = classify_test_report(_report("E   MemoryError"))
    assert classification.category == ErrorCategory.MEMORY_ERROR
    assert not classification.retryable


def test_runtime_error_is_retryable():
    classification = classify_test_report(_report("E   RecursionError: maximum recursion depth exceeded"))
    assert classification.category == ErrorCategory.RUNTIME_ERROR
    assert classification.retryable


def test_empty_patch_detected(workspace):
    classification = classify_patch_state(workspace)
    assert classification is not None
    assert classification.category == ErrorCategory.EMPTY_PATCH
    assert classification.retryable


def test_noop_patch_detected(workspace):
    workspace.write("python_programs/bitcount.py", workspace.read("python_programs/bitcount.py") + "\n")
    classification = classify_patch_state(workspace)
    assert classification is not None
    assert classification.category == ErrorCategory.NOOP_PATCH


def test_policy_retry_boundaries():
    policy = CorrectionPolicy(max_retries=2, style=FeedbackStyle.STRUCTURED)
    retryable = Classification(category=ErrorCategory.ASSERTION_FAILURE, retryable=True)
    fatal = Classification(category=ErrorCategory.TIMEOUT, retryable=False)
    assert policy.should_retry(retryable, 0)
    assert policy.should_retry(retryable, 1)
    assert not policy.should_retry(retryable, 2)
    assert not policy.should_retry(fatal, 0)


def test_policy_style_none_never_retries():
    policy = CorrectionPolicy(max_retries=2, style=FeedbackStyle.NONE)
    retryable = Classification(category=ErrorCategory.ASSERTION_FAILURE, retryable=True)
    assert not policy.should_retry(retryable, 0)


def test_style_mapping_from_settings(settings):
    assert settings.correction.style_for(Strategy.S4.value) == "structured"
    assert settings.correction.style_for(Strategy.S0.value) == "raw"

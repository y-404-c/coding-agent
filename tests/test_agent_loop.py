from __future__ import annotations

import json

from codingagent.agent.loop import AgentRunner
from codingagent.context import build_context_builder
from codingagent.domain.enums import ErrorCategory, Strategy
from codingagent.llm.scripted import ScriptedLLMClient, golden_bitcount_script
from codingagent.tools import build_default_registry


def _runner(settings, dataset, script, strategy=Strategy.S2) -> tuple[AgentRunner, ScriptedLLMClient]:
    client = ScriptedLLMClient(script)
    runner = AgentRunner(
        llm=client,
        settings=settings,
        builder=build_context_builder(strategy, dataset.load_all()),
        tools=build_default_registry(),
        support_files=dataset.support_files(),
    )
    return runner, client


def test_golden_script_fixes_bitcount(settings, dataset, bitcount):
    runner, _ = _runner(settings, dataset, golden_bitcount_script())
    trace = runner.run(bitcount, Strategy.S2)

    assert trace.passed
    assert trace.error_category == ErrorCategory.NONE
    assert len(trace.attempts) == 1
    step_actions = [step.action for step in trace.attempts[0].steps]
    assert step_actions == ["ast_slice", "edit_file", "run_test"]
    assert trace.attempts[0].stop_reason == "tests_passed"
    assert "n &= n - 1" in trace.final_patch
    assert trace.total_tokens > 0
    assert trace.localization_step == 1


def test_loop_guard_stops_repeated_call(settings, dataset, bitcount):
    script = [
        '{"action": "read_file", "args": {"path": "python_programs/bitcount.py"}, "reason": "读"}',
        '{"action": "read_file", "args": {"path": "python_programs/bitcount.py"}, "reason": "再读"}',
    ]
    runner, _ = _runner(settings, dataset, script)
    trace = runner.run(bitcount, Strategy.S2)

    attempt = trace.attempts[0]
    assert attempt.stop_reason == "loop_detected"
    assert attempt.degraded
    assert attempt.degrade_note and "patch 草稿" in attempt.degrade_note
    assert not trace.passed


def test_invalid_json_is_fed_back(settings, dataset, bitcount):
    script = [
        "这不是 JSON，我只是解释一下。",
        '{"action": "finish", "args": {}, "reason": "结束"}',
        '{"action": "finish", "args": {}, "reason": "结束"}',
    ]
    runner, client = _runner(settings, dataset, script)
    trace = runner.run(bitcount, Strategy.S2)

    attempt = trace.attempts[0]
    assert attempt.steps[0].action == "invalid_json"
    assert attempt.stop_reason == "finish"
    assert "could not be parsed" in client.calls[1][-1].content


def test_token_budget_is_independent_stop_condition(settings, dataset, bitcount):
    tight = settings.model_copy(deep=True)
    tight.agent.token_budget = 1
    # 关闭重试：否则后续尝试会走到 edit_file 并意外把题目修好，
    # 就测不出「预算耗尽」这一独立的停止条件了。
    tight.correction.max_retries = 0
    runner, client = _runner(tight, dataset, golden_bitcount_script())
    trace = runner.run(bitcount, Strategy.S2)

    assert client.calls  # 预算在调用前检查，允许最后一次调用超额
    assert trace.meta["final_stop_reason"] == "token_budget"
    assert trace.attempts[0].degraded
    assert not trace.passed


def test_structured_feedback_retry_recovers(settings, dataset):
    problem = dataset.load("gcd")
    source = "python_programs/gcd.py"
    wrong_edit = (
        '{"action": "edit_file", "args": {"path": "%s", '
        '"old": "    if b == 0:", "new": "    if b == 1:"}, "reason": "错误的边界条件"}'
        % source
    )
    right_edit = (
        '{"action": "edit_file", "args": {"path": "%s", '
        '"old": "        return gcd(a %% b, b)", "new": "        return gcd(b, a %% b)"}, '
        '"reason": "修正递归参数顺序"}'
        % source
    )
    run_test = '{"action": "run_test", "args": {}, "reason": "跑测试"}'
    finish = '{"action": "finish", "args": {}, "reason": "结束"}'
    script = [wrong_edit, run_test, finish, right_edit, run_test]
    runner, client = _runner(settings, dataset, script, strategy=Strategy.S4)
    trace = runner.run(problem, Strategy.S4)

    assert trace.passed
    assert len(trace.attempts) == 2
    first = trace.attempts[0]
    assert first.classification.category == ErrorCategory.RUNTIME_ERROR
    assert first.classification.retryable
    assert trace.meta["feedback_style"] == "structured"

    retry_messages = client.calls[3]
    user_content = retry_messages[1].content
    assert "结构化" in user_content
    assert "runtime_error" in user_content
    assert "relevant_code" in user_content


def test_raw_feedback_style_for_baseline(settings, dataset, bitcount):
    script = [
        '{"action": "read_file", "args": {"path": "python_programs/bitcount.py"}, "reason": "读"}',
        '{"action": "finish", "args": {}, "reason": "结束"}',
        '{"action": "finish", "args": {}, "reason": "结束"}',
    ]
    runner, client = _runner(settings, dataset, script, strategy=Strategy.S0)
    trace = runner.run(bitcount, Strategy.S0)
    assert trace.meta["feedback_style"] == "raw"
    assert trace.attempts[0].classification.category == ErrorCategory.EMPTY_PATCH
    retry_prompt = client.calls[-1][1].content
    assert "原始文本" in retry_prompt

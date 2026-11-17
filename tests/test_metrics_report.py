from __future__ import annotations

from datetime import datetime, timezone

from codingagent.domain.enums import ErrorCategory, Strategy
from codingagent.domain.models import AttemptRecord, Classification, RunTrace, StepRecord
from codingagent.eval.metrics import ablation_deltas, compute_metrics, failure_distribution
from codingagent.eval.report import build_markdown, render_metrics_table, write_reports


def _trace(
    strategy: Strategy,
    attempts_passed: list[bool],
    *,
    problem_id: str = "bitcount",
    repeat: int = 0,
    target_hit_step: int | None = 1,
    tokens: int = 300,
) -> RunTrace:
    steps = [StepRecord(step=1, action="read_file", target_hit=target_hit_step == 1)]
    attempts = [
        AttemptRecord(
            attempt=index + 1,
            steps=steps if index == 0 else [],
            passed=passed,
            classification=Classification(
                category=ErrorCategory.NONE if passed else ErrorCategory.ASSERTION_FAILURE,
                retryable=not passed,
            ),
            tokens=tokens,
            steps_used=3,
            stop_reason="finish",
        )
        for index, passed in enumerate(attempts_passed)
    ]
    return RunTrace(
        run_id=f"{strategy.value}-{problem_id}-{repeat}-{len(attempts_passed)}",
        strategy=strategy,
        problem_id=problem_id,
        repeat_index=repeat,
        attempts=attempts,
        passed=any(attempts_passed),
        error_category=ErrorCategory.NONE if any(attempts_passed) else ErrorCategory.ASSERTION_FAILURE,
        total_tokens=tokens * len(attempts),
        total_steps=3 * len(attempts),
        started_at=datetime.now(timezone.utc),
        meta={"final_stop_reason": "finish"},
    )


def test_pass_at_1_and_pass_at_2():
    traces = [
        _trace(Strategy.S0, [False], problem_id="a"),
        _trace(Strategy.S0, [False], problem_id="b"),
        _trace(Strategy.S1, [False, True], problem_id="a"),
        _trace(Strategy.S1, [True], problem_id="b"),
    ]
    metrics = compute_metrics(traces)
    assert metrics["S0"].pass_at_1 == 0.0
    assert metrics["S0"].pass_at_2 == 0.0
    assert metrics["S1"].pass_at_1 == 0.5
    assert metrics["S1"].pass_at_2 == 1.0


def test_localization_and_tokens():
    traces = [
        _trace(Strategy.S2, [True], problem_id="a", target_hit_step=1, tokens=100),
        _trace(Strategy.S2, [True], problem_id="b", target_hit_step=None, tokens=200),
    ]
    item = compute_metrics(traces)["S2"]
    assert item.localization_top1 == 1.0  # 只统计有命中的 run
    assert item.avg_tokens == 150


def test_failure_distribution():
    traces = [
        _trace(Strategy.S3, [False], problem_id="a"),
        _trace(Strategy.S3, [False], problem_id="b"),
        _trace(Strategy.S3, [True], problem_id="c"),
    ]
    distribution = failure_distribution(traces)
    assert distribution == {"assertion_failure": 2}


def test_ablation_deltas():
    traces = [
        _trace(Strategy.S0, [False], problem_id="a"),
        _trace(Strategy.S1, [True], problem_id="a"),
        _trace(Strategy.S2, [True], problem_id="a"),
    ]
    deltas = ablation_deltas(compute_metrics(traces))
    assert deltas["ast_slice"] == 1.0
    assert deltas["tests"] == 0.0
    assert deltas["fewshot"] is None


def test_empty_metrics_use_placeholder():
    table = render_metrics_table({})
    assert "___" not in table  # 没有策略行时只有表头
    markdown = build_markdown(
        [],
        model="deepseek-chat",
        dataset_count=40,
        repeats=3,
        prompt_version="test",
        config_hash="abc",
        db_path="var/runs.db",
    )
    assert "所有数字由" in markdown
    assert "___" in markdown


def test_write_reports(tmp_path):
    traces = [_trace(Strategy.S0, [False], problem_id="a")]
    paths = write_reports(
        traces,
        tmp_path,
        model="deepseek-chat",
        dataset_count=40,
        repeats=1,
        prompt_version="test",
        config_hash="abc",
        db_path="var/runs.db",
    )
    assert all(path.exists() for path in paths)
    csv_text = (tmp_path / "runs.csv").read_text(encoding="utf-8")
    assert "run_id,strategy,problem_id" in csv_text
    markdown = (tmp_path / "ab_results.md").read_text(encoding="utf-8")
    assert "| S0 | 0.0% |" in markdown

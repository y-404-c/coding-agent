from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from codingagent.domain.models import RunTrace


@dataclass
class StrategyMetrics:
    strategy: str
    runs: int = 0
    pass_at_1: float | None = None
    pass_at_2: float | None = None
    avg_tokens: float | None = None
    avg_steps: float | None = None
    avg_attempts: float | None = None
    localization_top1: float | None = None
    localization_top3: float | None = None
    degraded_runs: int = 0
    failure_distribution: dict[str, int] = field(default_factory=dict)


def compute_metrics(traces: Sequence[RunTrace]) -> dict[str, StrategyMetrics]:
    grouped: dict[str, list[RunTrace]] = {}
    for trace in traces:
        grouped.setdefault(trace.strategy.value, []).append(trace)

    result: dict[str, StrategyMetrics] = {}
    for strategy in sorted(grouped):
        items = grouped[strategy]
        runs = len(items)
        with_attempts = [item for item in items if item.attempts]
        result[strategy] = StrategyMetrics(
            strategy=strategy,
            runs=runs,
            pass_at_1=_ratio(with_attempts, lambda trace: trace.attempts[0].passed),
            pass_at_2=_ratio(with_attempts, lambda trace: trace.pass_at(2)),
            avg_tokens=_mean([trace.total_tokens for trace in items]),
            avg_steps=_mean([trace.total_steps for trace in items]),
            avg_attempts=_mean([len(trace.attempts) for trace in items]),
            localization_top1=_ratio(
                [trace for trace in items if trace.localization_step is not None],
                lambda trace: trace.localization_step == 1,
            ),
            localization_top3=_ratio(
                [trace for trace in items if trace.localization_step is not None],
                lambda trace: (trace.localization_step or 99) <= 3,
            ),
            degraded_runs=sum(
                1 for trace in items if any(attempt.degraded for attempt in trace.attempts)
            ),
            failure_distribution=failure_distribution(items),
        )
    return result


def failure_distribution(traces: Iterable[RunTrace]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trace in traces:
        if trace.passed:
            continue
        category = trace.error_category.value
        counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def ablation_deltas(metrics: dict[str, StrategyMetrics]) -> dict[str, float | None]:
    """把提升分解到每一步：S1-S0（切片）、S2-S1（测试）、S3-S2（few-shot）、S4-S3（回喂）。"""

    pairs = [("ast_slice", "S0", "S1"), ("tests", "S1", "S2"), ("fewshot", "S2", "S3"), ("feedback", "S3", "S4")]
    deltas: dict[str, float | None] = {}
    for label, lower, upper in pairs:
        low = metrics.get(lower)
        high = metrics.get(upper)
        if low and high and low.pass_at_1 is not None and high.pass_at_1 is not None:
            deltas[label] = high.pass_at_1 - low.pass_at_1
        else:
            deltas[label] = None
    return deltas


def _ratio(items: Sequence[RunTrace], predicate) -> float | None:
    if not items:
        return None
    return sum(1 for item in items if predicate(item)) / len(items)


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)

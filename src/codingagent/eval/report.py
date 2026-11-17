from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from codingagent.domain.models import RunTrace

from .metrics import StrategyMetrics, ablation_deltas, compute_metrics

PLACEHOLDER = "___"


def _pct(value: float | None) -> str:
    return PLACEHOLDER if value is None else f"{value * 100:.1f}%"


def _num(value: float | None, digits: int = 0) -> str:
    return PLACEHOLDER if value is None else f"{value:.{digits}f}"


def _pp(value: float | None) -> str:
    return PLACEHOLDER if value is None else f"{value * 100:+.1f} pp"


def render_metrics_table(metrics: dict[str, StrategyMetrics]) -> str:
    lines = [
        "| 策略 | pass@1 | pass@2（含自纠正） | 平均 token | 平均步数 | 定位 Top-1 | 定位 Top-3 | 运行数 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for strategy in sorted(metrics):
        item = metrics[strategy]
        lines.append(
            "| {s} | {p1} | {p2} | {tok} | {steps} | {t1} | {t3} | {runs} |".format(
                s=strategy,
                p1=_pct(item.pass_at_1),
                p2=_pct(item.pass_at_2),
                tok=_num(item.avg_tokens),
                steps=_num(item.avg_steps, 1),
                t1=_pct(item.localization_top1),
                t3=_pct(item.localization_top3),
                runs=item.runs,
            )
        )
    return "\n".join(lines)


def render_failure_table(metrics: dict[str, StrategyMetrics]) -> str:
    lines = [
        "| 策略 | 失败归因分布 | 降级运行数 |",
        "|---|---|---|",
    ]
    for strategy in sorted(metrics):
        item = metrics[strategy]
        distribution = (
            ", ".join(f"{name}={count}" for name, count in item.failure_distribution.items())
            or "—"
        )
        lines.append(f"| {strategy} | {distribution} | {item.degraded_runs} |")
    return "\n".join(lines)


def render_deltas(metrics: dict[str, StrategyMetrics]) -> str:
    deltas = ablation_deltas(metrics)
    return "\n".join(
        f"- {label}：{_pp(value)}" for label, value in deltas.items()
    )


def build_markdown(
    traces: Sequence[RunTrace],
    *,
    model: str,
    dataset_count: int,
    repeats: int,
    prompt_version: str,
    config_hash: str,
    db_path: str,
    source_note: str = "",
) -> str:
    metrics = compute_metrics(traces)
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    header = f"""# 上下文策略 A/B 结果

> 所有数字由 `ca report build` 从 SQLite trace 汇总，**未手工填写**。没有数据的单元格显示 `{PLACEHOLDER}`。

{render_metrics_table(metrics)}

## 提升分解（pass@1 差值）

{render_deltas(metrics)}

## 失败归因

{render_failure_table(metrics)}

## 实验条件

- 模型：`{model}`，temperature=0，prompt 版本 `{prompt_version}`
- 题集：QuixBugs Python 版，{dataset_count} 题
- 复现次数：{repeats}（每格为均值；抖动处理见 README「评测纪律」）
- 配置指纹：`{config_hash[:16]}...`（trace 表可按此过滤）
- 数据源：`{db_path}`
- 生成时间：{generated}
- 备注：{source_note or "无"}
"""
    return header


def write_reports(
    traces: Sequence[RunTrace],
    out_dir: Path,
    **meta,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = build_markdown(traces, **meta)
    md_path = out_dir / "ab_results.md"
    md_path.write_text(markdown, encoding="utf-8")

    csv_path = out_dir / "runs.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "run_id",
                "strategy",
                "problem_id",
                "repeat_index",
                "passed",
                "error_category",
                "total_tokens",
                "total_steps",
                "attempts",
                "final_stop_reason",
                "localization_step",
                "final_patch_chars",
            ]
        )
        for trace in traces:
            writer.writerow(
                [
                    trace.run_id,
                    trace.strategy.value,
                    trace.problem_id,
                    trace.repeat_index,
                    int(trace.passed),
                    trace.error_category.value,
                    trace.total_tokens,
                    trace.total_steps,
                    len(trace.attempts),
                    trace.meta.get("final_stop_reason", ""),
                    trace.localization_step,
                    len(trace.final_patch),
                ]
            )

    summary_path = out_dir / "summary.json"
    metrics = compute_metrics(traces)
    summary_path.write_text(
        json.dumps(
            {
                "model": meta.get("model"),
                "config_hash": meta.get("config_hash"),
                "metrics": {
                    name: {
                        "runs": item.runs,
                        "pass_at_1": item.pass_at_1,
                        "pass_at_2": item.pass_at_2,
                        "avg_tokens": item.avg_tokens,
                        "avg_steps": item.avg_steps,
                        "localization_top1": item.localization_top1,
                        "localization_top3": item.localization_top3,
                        "degraded_runs": item.degraded_runs,
                        "failure_distribution": item.failure_distribution,
                    }
                    for name, item in metrics.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return [md_path, csv_path, summary_path]

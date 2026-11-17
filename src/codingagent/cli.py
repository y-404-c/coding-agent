from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codingagent.agent.loop import AgentRunner
from codingagent.agent.prompts import PROMPT_VERSION
from codingagent.config import PROJECT_ROOT, Settings
from codingagent.context import build_context_builder
from codingagent.dataset import DatasetError, QuixBugsDataset
from codingagent.domain.enums import Strategy
from codingagent.domain.models import Problem
from codingagent.eval.harness import EvaluationHarness
from codingagent.eval.report import write_reports
from codingagent.eval.runner import ExperimentRunner
from codingagent.llm import LLMError, ScriptedLLMClient, build_llm, golden_bitcount_script
from codingagent.sandbox.runner import TestRunner
from codingagent.storage import TraceRepository
from codingagent.tools import build_default_registry


def _settings(args: argparse.Namespace) -> Settings:
    return Settings.load(getattr(args, "config", None))


def _dataset(settings: Settings) -> QuixBugsDataset:
    return QuixBugsDataset(
        settings.dataset.root,
        source_url=settings.dataset.source_url,
        auto_fetch=settings.dataset.auto_fetch,
    )


def _strategies(value: str | None, settings: Settings) -> list[str]:
    raw = value or ",".join(settings.eval.strategies)
    parsed = [item.strip().upper() for item in raw.split(",") if item.strip()]
    valid = {item.value for item in Strategy}
    bad = [item for item in parsed if item not in valid]
    if bad:
        raise SystemExit(f"未知策略: {bad}，可选 {sorted(valid)}")
    return parsed


def _problem_ids(value: str | None, dataset: QuixBugsDataset, limit: int | None) -> list[str]:
    ids = dataset.problem_ids()
    if value and value != "all":
        wanted = [item.strip() for item in value.split(",") if item.strip()]
        unknown = [item for item in wanted if item not in ids]
        if unknown:
            raise SystemExit(f"未知题目: {unknown}")
        ids = wanted
    return ids[:limit] if limit else ids


def _apply_priority(problem_ids: list[str], priority_file: str | None) -> list[str]:
    """按项目二 watchlist export 的顺序重排题目，未在优先级列表中的题保持原序追加。"""

    if not priority_file:
        return problem_ids
    path = Path(priority_file)
    if not path.exists():
        raise SystemExit(f"优先级文件不存在: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    ordered = [str(item) for item in payload.get("problem_ids", []) if str(item) in problem_ids]
    remaining = [pid for pid in problem_ids if pid not in ordered]
    return [*ordered, *remaining]


# --------------------------------------------------------------------------- dataset
def cmd_dataset_fetch(args: argparse.Namespace) -> int:
    settings = _settings(args)
    dataset = _dataset(settings)
    dataset.fetch(force=args.force)
    manifest = dataset.manifest()
    print(f"数据集目录: {dataset.root}")
    print(f"题目数: {manifest['count']}  digest: {manifest['digest'][:16]}...")
    print(f"未配对: {manifest['unpaired']}")
    return 0


def cmd_dataset_verify(args: argparse.Namespace) -> int:
    settings = _settings(args)
    dataset = _dataset(settings)
    print(json.dumps(dataset.verify(), ensure_ascii=False, indent=2))
    return 0


def cmd_dataset_list(args: argparse.Namespace) -> int:
    settings = _settings(args)
    dataset = _dataset(settings)
    ids = dataset.problem_ids()
    for problem_id in ids:
        print(problem_id)
    print(f"共 {len(ids)} 题")
    return 0


def cmd_dataset_smoke(args: argparse.Namespace) -> int:
    """如实记录可运行性：题目的 buggy 版应失败、correct 版应通过。"""

    settings = _settings(args)
    dataset = _dataset(settings)
    support = dataset.support_files()
    ids = _problem_ids(args.problems, dataset, args.limit)
    rows: list[tuple[str, str, str]] = []
    anomalies: list[str] = []
    for index, problem_id in enumerate(ids, start=1):
        problem = dataset.load(problem_id)
        runner = TestRunner(problem, support, settings.sandbox, correct_program=problem.correct_code)
        buggy = runner.run({problem.entry_path: problem.buggy_code})
        correct = runner.run({problem.entry_path: problem.buggy_code}, use_correct=True)
        status = "ok"
        if buggy.ok:
            status = "buggy_passes"
            anomalies.append(f"{problem_id}: buggy 版本直接通过测试")
        elif not correct.ok:
            status = "correct_fails"
            anomalies.append(f"{problem_id}: correct 版本未通过测试")
        rows.append((problem_id, f"{buggy.passed}/{buggy.failed}/{buggy.errors}", status))
        print(f"[{index}/{len(ids)}] {problem_id:<32} {status}")

    out_dir = Path(settings.eval.reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 数据集可运行性自检",
        "",
        f"- 题数: {len(rows)}",
        f"- 异常题: {len(anomalies)}",
        "",
        "| 题目 | buggy 测试(通过/失败/错误) | 结论 |",
        "|---|---|---|",
    ]
    lines += [f"| {pid} | {stat} | {status} |" for pid, stat, status in rows]
    if anomalies:
        lines += ["", "## 异常明细", ""] + [f"- {item}" for item in anomalies]
    path = out_dir / "dataset_smoke.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n可运行: {len(rows) - len(anomalies)}/{len(rows)}；报告: {path}")
    return 0


# --------------------------------------------------------------------------- agent
def cmd_agent_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    dataset = _dataset(settings)
    problem = dataset.load(args.problem)
    llm = build_llm(settings.llm)
    strategy = Strategy(args.strategy.upper())
    runner = AgentRunner(
        llm=llm,
        settings=settings,
        builder=build_context_builder(strategy, dataset.load_all()),
        tools=build_default_registry(),
        support_files=dataset.support_files(),
    )
    trace = runner.run(problem, strategy, repeat_index=0, config_hash="adhoc")
    TraceRepository(settings.eval.db_path).save(trace)

    if args.json:
        print(trace.model_dump_json(indent=2))
        return 0 if trace.passed else 1

    print(f"题目 {problem.problem_id} / 策略 {trace.strategy.value} / pass={trace.passed}")
    print(f"tokens={trace.total_tokens} steps={trace.total_steps} "
          f"category={trace.error_category.value}")
    for attempt in trace.attempts:
        print(f"\n--- attempt {attempt.attempt} stop={attempt.stop_reason} "
              f"passed={attempt.passed} ---")
        for step in attempt.steps:
            print(f"  [{step.step}] {step.action} ok={step.ok} "
                  f"tokens={step.tokens} hit={step.target_hit}")
            if args.show_observation:
                print("      obs: " + step.observation.replace("\n", "\n           ")[:600])
        if attempt.patch_text:
            print(attempt.patch_text)
        if attempt.degrade_note:
            print(attempt.degrade_note)
    return 0 if trace.passed else 1


# --------------------------------------------------------------------------- eval
def cmd_eval_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    dataset = _dataset(settings)
    if args.db:
        settings.eval.db_path = Path(args.db)
    if settings.llm.provider == "scripted" and not args.allow_scripted:
        raise SystemExit(
            "拒绝用 scripted 客户端产出正式指标：它只验证 harness 通路，不代表真实结果。"
            "自检请用 `ca selftest`；确需写入请加 --allow-scripted。"
        )
    llm = build_llm(settings.llm)
    strategies = _strategies(args.strategies, settings)
    problem_ids = _apply_priority(
        _problem_ids(args.problems, dataset, args.limit), args.priority
    )
    repeats = args.reps or settings.eval.repeats
    runner = ExperimentRunner(settings, dataset, llm)
    runner.run(
        strategies=strategies,
        problem_ids=problem_ids,
        repeats=repeats,
        resume=not args.no_resume,
    )

    repository = TraceRepository(settings.eval.db_path)
    traces = repository.list_traces(config_hash=runner.config_hash())
    if traces:
        meta = dict(
            model=settings.llm.model,
            dataset_count=len(dataset.problem_ids()),
            repeats=repeats,
            prompt_version=PROMPT_VERSION,
            config_hash=runner.config_hash(),
            db_path=str(settings.eval.db_path),
            source_note=f"{len(traces)} 条 trace",
        )
        for path in write_reports(traces, Path(settings.eval.reports_dir), **meta):
            print(f"写出: {path}")
        print("\n" + (Path(settings.eval.reports_dir) / "ab_results.md").read_text(encoding="utf-8"))
    return 0


def cmd_eval_regrade(args: argparse.Namespace) -> int:
    """独立复算：把历史 patch 重新应用到原始代码再跑官方测试。"""

    settings = _settings(args)
    dataset = _dataset(settings)
    repository = TraceRepository(Path(args.db) if args.db else settings.eval.db_path)
    traces = repository.list_traces(strategy=args.strategy, limit=args.limit)
    if not traces:
        print("没有可复算的 trace")
        return 1
    harness = EvaluationHarness(dataset, settings)
    mismatches = 0
    for index, trace in enumerate(traces, start=1):
        report, passed = harness.regrade_trace(trace)
        if passed != trace.passed:
            mismatches += 1
            print(f"[MISMATCH] {trace.run_id} {trace.problem_id} "
                  f"记录={trace.passed} 复算={passed}")
        elif index % 20 == 0:
            print(f"[{index}/{len(traces)}] 已完成")
    print(f"复算 {len(traces)} 条，判定不一致 {mismatches} 条")
    return 0 if mismatches == 0 else 1


def cmd_report_build(args: argparse.Namespace) -> int:
    settings = _settings(args)
    repository = TraceRepository(Path(args.db) if args.db else settings.eval.db_path)
    traces = repository.list_traces(strategy=args.strategy)
    if not traces:
        print("数据库为空，无法生成报告")
        return 1
    try:
        dataset_count = len(_dataset(settings).problem_ids())
    except DatasetError:
        dataset_count = len({trace.problem_id for trace in traces})
    meta = dict(
        model=traces[-1].model,
        dataset_count=dataset_count,
        repeats=max(trace.repeat_index for trace in traces) + 1,
        prompt_version=traces[-1].prompt_version,
        config_hash=traces[-1].config_hash,
        db_path=str(repository.db_path),
        source_note=f"{len(traces)} 条 trace",
    )
    out_dir = Path(args.out) if args.out else Path(settings.eval.reports_dir)
    for path in write_reports(traces, out_dir, **meta):
        print(f"写出: {path}")
    print("\n" + (out_dir / "ab_results.md").read_text(encoding="utf-8"))
    return 0


# --------------------------------------------------------------------------- selftest
def cmd_selftest(args: argparse.Namespace) -> int:
    """离线自检：用脚本化客户端跑通 harness 全链路（不产生真实指标）。"""

    settings = _settings(args)
    dataset = _dataset(settings)
    settings.eval.db_path = PROJECT_ROOT / "var" / "selftest.db"
    settings.eval.db_path.parent.mkdir(parents=True, exist_ok=True)
    if settings.eval.db_path.exists():
        settings.eval.db_path.unlink()

    problem = dataset.load("bitcount")
    llm = ScriptedLLMClient(golden_bitcount_script())
    runner = AgentRunner(
        llm=llm,
        settings=settings,
        builder=build_context_builder(Strategy.S2, dataset.load_all()),
        tools=build_default_registry(),
        support_files=dataset.support_files(),
    )
    trace = runner.run(problem, Strategy.S2, config_hash="selftest")
    TraceRepository(settings.eval.db_path).save(trace)

    print(f"selftest: problem=bitcount strategy=S2 pass={trace.passed} "
          f"tokens={trace.total_tokens} steps={trace.total_steps}")
    for step in trace.attempts[0].steps:
        print(f"  [{step.step}] {step.action} ok={step.ok} tokens={step.tokens}")
    if trace.final_patch:
        print(trace.final_patch)
    if not trace.passed:
        print("单调自检失败：请检查沙箱与数据集")
        return 1
    print("链路正常：Agent Loop → 工具 → patch → 沙箱测试 → 落库")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from codingagent.api.app import create_app

    uvicorn.run(create_app(_settings(args)), host=args.host, port=args.port)
    return 0


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", help="YAML 配置文件路径", default=None)

    parser = argparse.ArgumentParser(
        prog="ca", description="Coding Agent + 上下文工程受控实验"
    )
    parser.add_argument("-c", "--config", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    dataset = sub.add_parser("dataset", help="数据集管理", parents=[common])
    dataset_sub = dataset.add_subparsers(dest="dataset_command", required=True)
    fetch = dataset_sub.add_parser("fetch", parents=[common])
    fetch.add_argument("--force", action="store_true")
    fetch.set_defaults(func=cmd_dataset_fetch)
    verify = dataset_sub.add_parser("verify", parents=[common])
    verify.set_defaults(func=cmd_dataset_verify)
    list_cmd = dataset_sub.add_parser("list", parents=[common])
    list_cmd.set_defaults(func=cmd_dataset_list)
    smoke = dataset_sub.add_parser("smoke", parents=[common], help="跑 buggy/correct 官方测试")
    smoke.add_argument("--problems", default="all")
    smoke.add_argument("--limit", type=int, default=None)
    smoke.set_defaults(func=cmd_dataset_smoke)

    agent = sub.add_parser("agent", help="单题运行 Agent", parents=[common])
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_run = agent_sub.add_parser("run", parents=[common])
    agent_run.add_argument("--problem", required=True)
    agent_run.add_argument("--strategy", default="S2")
    agent_run.add_argument("--json", action="store_true")
    agent_run.add_argument("--show-observation", action="store_true")
    agent_run.set_defaults(func=cmd_agent_run)

    eval_parser = sub.add_parser("eval", help="受控实验", parents=[common])
    eval_sub = eval_parser.add_subparsers(dest="eval_command", required=True)
    eval_run = eval_sub.add_parser("run", parents=[common])
    eval_run.add_argument("--strategies", default=None)
    eval_run.add_argument("--problems", default="all")
    eval_run.add_argument("--reps", type=int, default=None)
    eval_run.add_argument("--limit", type=int, default=None)
    eval_run.add_argument("--no-resume", action="store_true")
    eval_run.add_argument("--db", default=None)
    eval_run.add_argument("--allow-scripted", action="store_true")
    eval_run.add_argument(
        "--priority",
        default=None,
        help="项目二 watchlist export 产出的 priority_problems.json，按其顺序优先跑高频失败题",
    )
    eval_run.set_defaults(func=cmd_eval_run)
    regrade = eval_sub.add_parser("regrade", parents=[common], help="用 patch 独立复算")
    regrade.add_argument("--db", default=None)
    regrade.add_argument("--strategy", default=None)
    regrade.add_argument("--limit", type=int, default=None)
    regrade.set_defaults(func=cmd_eval_regrade)

    report = sub.add_parser("report", help="报告", parents=[common])
    report_sub = report.add_subparsers(dest="report_command", required=True)
    report_build = report_sub.add_parser("build", parents=[common])
    report_build.add_argument("--db", default=None)
    report_build.add_argument("--out", default=None)
    report_build.add_argument("--strategy", default=None)
    report_build.set_defaults(func=cmd_report_build)

    selftest = sub.add_parser("selftest", help="离线自检", parents=[common])
    selftest.set_defaults(func=cmd_selftest)

    serve = sub.add_parser("serve", help="启动 FastAPI", parents=[common])
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (DatasetError, LLMError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

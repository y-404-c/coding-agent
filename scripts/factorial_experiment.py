"""HumanEvalFix 析因对照实验：拆解 bare→Agent 差异的因果归属。

背景（为什么要这个脚本）：
  之前 QuixBugs 的 bare(94.9%) vs Agent(98.3~100%) 只差 5pp，且 bare 是
  "单 prompt 无工具无循环"，Agent 是"带工具带循环只换上下文"，同时变了
  三个变量，5pp 的差异无法归因到循环、工具或上下文中的任何一个。

本实验把变量拆开，四个条件共享同模型 / 同冻结题集 / 同官方 pytest 判定：

  B0  bare            : 单 prompt，无工具、无循环、无反馈          —— 绝对下限
  B1  bare+feedback   : 单 prompt + 测试报错回喂重试，仍无工具/策略 —— 隔离"循环+反馈"
  A0  agent-full      : ReAct 工具循环 + 全文件上下文(S0)          —— 加"工具+循环"
  A1  agent-slice     : ReAct 工具循环 + AST 切片上下文(S1)        —— 加"上下文策略"

可回答的因果问题：
  B1 - B0  = 只加"循环重试 + 测试反馈"的净贡献（不动工具、不动上下文）
  A0 - B0  = 加"整套 ReAct 工具循环（全文件）"的总贡献
  A1 - A0  = 在完整 Agent 内换"上下文策略（AST 切片）"的净贡献
  A1 - B1  = 在同样有反馈的前提下，"工具+上下文"相比"纯回喂"的增量

判定物：全部复用同一 TestRunner（官方 pytest 隔离目录），不是 patch 文本相似度。
API 超时/环境异常单独标记 env_error，不冒充能力失败。

用法：
  .venv/Scripts/python.exe scripts/factorial_experiment.py --limit 5 --conditions B0,B1
  .venv/Scripts/python.exe scripts/factorial_experiment.py --conditions B0,B1,A0,A1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codingagent.agent.loop import AgentRunner  # noqa: E402
from codingagent.agent.prompts import PROMPT_VERSION  # noqa: E402
from codingagent.config import Settings  # noqa: E402
from codingagent.context import build_context_builder  # noqa: E402
from codingagent.dataset.humanevalfix import HumanEvalFixDataset, restore_imports  # noqa: E402
from codingagent.domain.enums import ErrorCategory, Strategy  # noqa: E402
from codingagent.domain.models import ChatMessage  # noqa: E402
from codingagent.llm.openai_compatible import OpenAICompatibleClient  # noqa: E402
from codingagent.sandbox.grading import build_runner  # noqa: E402
from codingagent.tools import build_default_registry  # noqa: E402

CONDITIONS = ("B0", "B1", "A0", "A1")

SYSTEM = (
    "You are a Python program repair assistant. "
    "You will be given a buggy Python function. "
    "Return ONLY the corrected function definition (from 'def' through its body). "
    "Do NOT repeat the docstring or comments after the function. "
    "Do not explain. Do not use tools. Output only the fixed code."
)
USER_TMPL = "Fix the bug in this Python function. Output ONLY the corrected function.\n\n```python\n{buggy}\n```"
FEEDBACK_TMPL = (
    "Your previous fix FAILED the official tests. Here is the test report:\n"
    "---\n{report}\n---\n"
    "Return ONLY the corrected function definition again. Do not explain."
)

_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code(text: str, function_name: str) -> str:
    blocks = _FENCE.findall(text)
    for candidate in list(blocks) + [text]:
        cleaned = candidate.strip()
        if re.search(r"\bdef\s+" + re.escape(function_name) + r"\s*\(", cleaned):
            # 截断到函数结束：只保留 def 开始到文档字符串之前的部分由调用方处理
            return cleaned + "\n"
    return (blocks[0] if blocks else text).strip() + "\n"


def render_test_report(report) -> str:
    parts = [
        f"passed={report.passed} failed={report.failed} errors={report.errors}",
    ]
    if report.failed_tests:
        parts.append("failed tests: " + ", ".join(report.failed_tests[:5]))
    if report.error_types:
        parts.append("error types: " + ", ".join(report.error_types[:5]))
    if report.assertion_lines:
        parts.append("assertions: " + " | ".join(report.assertion_lines[:5]))
    if report.output_tail:
        parts.append("output: " + report.output_tail[-600:].replace("\n", " "))
    return "\n".join(parts)


def run_bare(problem, client, runner, *, with_feedback: bool, max_rounds: int = 3) -> dict:
    """B0/B1：单 prompt 生成，可选测试报错回喂重试。无工具、无上下文策略。"""
    total_tokens = 0
    rounds = []
    messages = [
        ChatMessage(role="system", content=SYSTEM),
        ChatMessage(role="user", content=USER_TMPL.format(buggy=problem.buggy_code)),
    ]
    last_fixed = ""
    for rnd in range(max_rounds if with_feedback else 1):
        try:
            response = client.complete(messages)
        except Exception as exc:  # noqa: BLE001
            return {
                "passed": False,
                "total_tokens": total_tokens,
                "rounds": rounds,
                "env_error": f"{type(exc).__name__}: {str(exc)[:200]}",
                "fixed_code": last_fixed[:2000],
            }
        total_tokens += response.total_tokens
        extracted = extract_code(response.text, problem.function_name)
        # 裸函数补回原文件的导入头（from typing import List 等），否则 NameError
        last_fixed = restore_imports(problem.buggy_code, extracted, problem.function_name)
        test_report = runner.run({problem.entry_path: last_fixed})
        rounds.append(
            {
                "round": rnd + 1,
                "passed": bool(test_report.ok),
                "tokens": response.total_tokens,
                "latency_ms": response.latency_ms,
                "test_passed": test_report.passed,
                "test_failed": test_report.failed,
            }
        )
        if test_report.ok:
            return {
                "passed": True,
                "total_tokens": total_tokens,
                "rounds": rounds,
                "env_error": "",
                "fixed_code": last_fixed[:2000],
            }
        if not with_feedback or rnd == max_rounds - 1:
            break
        messages = messages + [
            ChatMessage(role="assistant", content=response.text[:1000]),
            ChatMessage(
                role="user",
                content=FEEDBACK_TMPL.format(report=render_test_report(test_report)),
            ),
        ]
    return {
        "passed": False,
        "total_tokens": total_tokens,
        "rounds": rounds,
        "env_error": "",
        "fixed_code": last_fixed[:2000],
    }


def run_agent(problem, strategy: Strategy, settings, llm, dataset, support_files) -> dict:
    """A0/A1：复用现成 AgentRunner，仅切换 context_builder。"""
    builder = build_context_builder(strategy, dataset.load_all())
    tools = build_default_registry()
    runner = AgentRunner(
        llm=llm,
        settings=settings,
        builder=builder,
        tools=tools,
        support_files=support_files,
    )
    trace = runner.run(problem, strategy, repeat_index=0, config_hash="factorial-v1")
    return {
        "passed": bool(trace.passed),
        "total_tokens": trace.total_tokens,
        "total_steps": trace.total_steps,
        "attempts": len(trace.attempts),
        "error_category": trace.error_category.value,
        "env_error": (
            trace.error_category.value if trace.error_category == ErrorCategory.ENV_ERROR else ""
        ),
        "fixed_code": trace.final_patch[:2000],
    }


def load_done(path: Path) -> dict[tuple[str, str], dict]:
    done: dict[tuple[str, str], dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                done[(row["problem_id"], row["condition"])] = row
            except Exception:
                continue
    return done


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conditions", default=",".join(CONDITIONS))
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟）")
    parser.add_argument("--only", default="", help="逗号分隔题号")
    parser.add_argument("--subset", type=int, default=50, help="冻结子集大小（0=全部164）")
    parser.add_argument("--seed", type=int, default=20261121)
    parser.add_argument("--out", default="reports/factorial_humanevalfix.jsonl")
    parser.add_argument("--max-rounds", type=int, default=3, help="B1 最大回喂轮数")
    args = parser.parse_args()

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    settings = Settings.load()
    dataset = HumanEvalFixDataset(
        settings.dataset.root if False else "data/raw/humanevalfix/humanevalpack_python.json",
        subset_size=args.subset,
        seed=args.seed,
    )
    problem_ids = dataset.problem_ids()
    if args.only:
        wanted = set(args.only.split(","))
        problem_ids = [pid for pid in problem_ids if pid in wanted]
    if args.limit:
        problem_ids = problem_ids[: args.limit]

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = Path(__file__).resolve().parents[1] / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(out_path)

    client = OpenAICompatibleClient(settings.llm)
    support_files = dataset.support_files()
    manifest = dataset.manifest()
    print(f"MODEL={settings.llm.model} KEY={settings.llm.resolved_api_key()[:8]}...")
    print(f"DATASET=humanevalfix subset={manifest['size']}/{manifest.get('total','?')} seed={args.seed}")
    print(f"CONDITIONS={conditions} N={len(problem_ids)} OUT={out_path.name}")
    print(f"RESUME: {len(done)} records already done")

    for problem_id in problem_ids:
        problem = dataset.load(problem_id)
        for cond in conditions:
            if (problem_id, cond) in done:
                print(f"  {problem_id:10s} {cond} SKIP(done)")
                continue
            t0 = datetime.now(timezone.utc)
            if cond in ("B0", "B1"):
                runner = build_runner(problem, support_files, settings.sandbox)
                result = run_bare(
                    problem,
                    client,
                    runner,
                    with_feedback=(cond == "B1"),
                    max_rounds=args.max_rounds,
                )
            elif cond == "A0":
                result = run_agent(problem, Strategy.S0, settings, client, dataset, support_files)
            elif cond == "A1":
                result = run_agent(problem, Strategy.S1, settings, client, dataset, support_files)
            else:
                raise ValueError(f"未知条件: {cond}")

            record = {
                "problem_id": problem_id,
                "condition": cond,
                "passed": bool(result["passed"]),
                "total_tokens": result.get("total_tokens", 0),
                "total_steps": result.get("total_steps", 0),
                "rounds": result.get("rounds", []),
                "attempts": result.get("attempts", 0),
                "env_error": result.get("env_error", ""),
                "error_category": result.get("error_category", ""),
                "fixed_code": result.get("fixed_code", ""),
                "model": settings.llm.model,
                "prompt_version": PROMPT_VERSION,
                "dataset_digest": manifest["digest"],
                "created_at": t0.isoformat(),
            }
            with out_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            done[(problem_id, cond)] = record
            print(
                f"  {problem_id:10s} {cond} pass={record['passed']} "
                f"tok={record['total_tokens']} steps={record['total_steps']} "
                f"env={record['env_error'][:40]}"
            )

    # 实时汇总
    rows = list(done.values())
    print("\n=== SUMMARY (current) ===")
    for cond in conditions:
        subset = [r for r in rows if r["condition"] == cond]
        if not subset:
            continue
        passed = sum(1 for r in subset if r["passed"])
        avg_tok = sum(r["total_tokens"] for r in subset) / len(subset)
        env = sum(1 for r in subset if r["env_error"])
        print(f"{cond}: {passed}/{len(subset)} = {passed/len(subset):.1%}  avg_tok={avg_tok:.0f}  env_err={env}")


if __name__ == "__main__":
    main()

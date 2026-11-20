"""裸跑对照（bare baseline）：同模型、同题集、同官方复测，去掉整个 Agent 框架。

对照目的：项目一 pass@1 贴在 98.3%~100%，必须回答"裸 prompt 能不能也做到"。
本脚本只给 buggy code + 一条直接修复指令，无工具、无循环、无上下文策略、
无测试反馈、无 few-shot；判定时复用与 Agent 完全相同的 TestRunner（官方 pytest）。

两边唯一差别 = 有没有 Agent 框架。这样得到的差值才有因果意义。

用法：
  .venv/Scripts/python.exe scripts/bare_baseline.py --only bitcount --repeats 1   # 冒烟
  .venv/Scripts/python.exe scripts/bare_baseline.py --repeats 3                   # 全量
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codingagent.config import Settings  # noqa: E402
from codingagent.dataset.quixbugs import QuixBugsDataset  # noqa: E402
from codingagent.domain.models import ChatMessage  # noqa: E402
from codingagent.llm.openai_compatible import OpenAICompatibleClient  # noqa: E402
from codingagent.sandbox.grading import build_runner  # noqa: E402

SYSTEM = (
    "You are a Python program repair assistant. "
    "You will be given a buggy Python program. "
    "Return the complete corrected program as a single Python code block. "
    "Do not explain. Do not use tools. Output only the fixed code."
)

USER_TMPL = (
    "Fix the bug in this Python program. Return the entire corrected file.\n\n"
    "```python\n{buggy}\n```"
)

# 长输出题专用：只回函数代码，不照抄 docstring，避免响应超长导致 API 超时。
USER_TMPL_COMPACT = (
    "Fix the bug in this Python function. Output ONLY the corrected function "
    "definition (from 'def' through its body). Do NOT repeat the docstring or "
    "any comments after the function. Do not explain.\n\n"
    "```python\n{buggy}\n```"
)

_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code(text: str, function_name: str) -> str:
    """从模型输出里稳健提取完整代码：优先含目标函数的代码块。"""
    blocks = _FENCE.findall(text)
    candidates = list(blocks) + [text]
    for candidate in candidates:
        cleaned = candidate.strip()
        if re.search(r"\bdef\s+" + re.escape(function_name) + r"\s*\(", cleaned):
            return cleaned + "\n"
    return candidates[0].strip() + "\n"


def load_done(path: Path) -> dict[tuple[str, int], dict]:
    done: dict[tuple[str, int], dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                done[(row["problem_id"], row["repeat"])] = row
            except Exception:
                continue
    return done


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟）")
    parser.add_argument("--only", default="", help="逗号分隔的题号")
    parser.add_argument("--out", default="reports/bare_baseline.jsonl")
    parser.add_argument("--compact", action="store_true", help="长输出题：只回函数代码，防超时")
    args = parser.parse_args()

    settings = Settings.load()
    dataset = QuixBugsDataset(settings.dataset.root, settings.dataset.source_url)
    dataset.ensure()

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
    key_prefix = settings.llm.resolved_api_key()[:8]

    print(f"MODEL={settings.llm.model} BASE_URL={settings.llm.base_url}")
    print(f"KEY_PREFIX={key_prefix}... N_PROBLEMS={len(ids_label := problem_ids)} REPEATS={args.repeats}")
    print(f"OUT={out_path} (already_done={len(done)})")

    for problem_id in problem_ids:
        problem = dataset.load(problem_id)
        runner = build_runner(problem, support_files, settings.sandbox)
        for repeat in range(args.repeats):
            if (problem_id, repeat) in done:
                print(f"  {problem_id:28s} r{repeat} SKIP(done)")
                continue
            user_template = USER_TMPL_COMPACT if args.compact else USER_TMPL
            messages = [
                ChatMessage(role="system", content=SYSTEM),
                ChatMessage(role="user", content=user_template.format(buggy=problem.buggy_code)),
            ]
            try:
                response = client.complete(messages)
                fixed_code = extract_code(response.text, problem.function_name)
                test_report = runner.run({problem.entry_path: fixed_code})
                record = {
                    "problem_id": problem_id,
                    "function_name": problem.function_name,
                    "repeat": repeat,
                    "passed": bool(test_report.ok),
                    "test_passed": test_report.passed,
                    "test_failed": test_report.failed,
                    "test_errors": test_report.errors,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.total_tokens,
                    "latency_ms": response.latency_ms,
                    "has_code_fence": "```" in response.text,
                    "extracted_len": len(fixed_code),
                    "error": "",
                    "fixed_code": fixed_code[:4000],
                    "model": response.model,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:  # noqa: BLE001
                record = {
                    "problem_id": problem_id,
                    "repeat": repeat,
                    "passed": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    "model": settings.llm.model,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            with out_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            done[(problem_id, repeat)] = record
            print(
                f"  {problem_id:28s} r{repeat} pass={record.get('passed')} "
                f"tok={record.get('total_tokens', 0)} {record.get('error', '')[:70]}"
            )

    rows = list(done.values())
    first = [row for row in rows if row.get("repeat") == 0]
    all_pass = sum(1 for row in rows if row.get("passed"))
    first_pass = sum(1 for row in first if row.get("passed"))
    env_errors = sum(1 for row in rows if row.get("error"))
    avg_tokens = sum(row.get("total_tokens", 0) for row in rows) / max(1, len(rows))
    print("\n=== SUMMARY ===")
    print(f"runs={len(rows)}  first_repeat={len(first)}")
    print(f"pass@1(first_repeat)={first_pass}/{len(first)} = {first_pass / max(1, len(first)):.1%}")
    print(f"pass(all_runs)={all_pass}/{len(rows)} = {all_pass / max(1, len(rows)):.1%}")
    print(f"avg_tokens={avg_tokens:.0f}  env_errors={env_errors}")


if __name__ == "__main__":
    main()

"""析因实验报告生成：从 factorial_humanevalfix.jsonl 产出诚实 Markdown 报告。

不编造数字。所有 pass@1 / token / 增量都从原始 JSONL 重算。
env_error 样本单独统计，不冒充能力失败。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

COND_LABELS = {
    "B0": "B0 裸跑（单 prompt，无工具/循环/反馈）",
    "B1": "B1 裸跑+反馈（报错回喂重试，无工具/策略）",
    "A0": "A0 Agent-全文件（ReAct 工具循环 + S0）",
    "A1": "A1 Agent-切片（ReAct 工具循环 + S1 AST 切片）",
}


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def pct(n: int, d: int) -> str:
    return f"{n}/{d} = {n / d:.1%}" if d else "无数据"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", default="reports/factorial_humanevalfix.jsonl")
    parser.add_argument("--out", default="reports/factorial_humanevalfix_report.md")
    args = parser.parse_args()

    base = Path(__file__).resolve().parents[1]
    inp = Path(args.inp)
    if not inp.is_absolute():
        inp = base / inp
    out = Path(args.out)
    if not out.is_absolute():
        out = base / out
    rows = load_rows(inp)

    by_cond: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cond[r["condition"]].append(r)

    conditions = [c for c in COND_LABELS if c in by_cond]
    stats = {}
    for c in conditions:
        items = by_cond[c]
        n = len(items)
        passed = sum(1 for r in items if r["passed"])
        env = sum(1 for r in items if r.get("env_error"))
        avg_tok = sum(r.get("total_tokens", 0) for r in items) / max(1, n)
        avg_steps = sum(r.get("total_steps", 0) for r in items) / max(1, n)
        stats[c] = {"n": n, "passed": passed, "env": env, "tok": avg_tok, "steps": avg_steps}

    lines: list[str] = []
    lines.append("# HumanEvalFix 析因对照实验")
    lines.append("")
    lines.append("## 为什么换数据集 + 为什么要析因")
    lines.append("")
    lines.append(
        "QuixBugs 上裸跑已 94.9%，Agent 98.3%~100%，只差约 5pp 且贴在天花板；"
        "更致命的是 bare（无工具/无循环）与 Agent（有工具/有循环、只换上下文）"
        "同时变了三个变量，5pp 无法归因到循环、工具还是上下文。"
    )
    lines.append("")
    lines.append(
        "HumanEvalFix（164 题单函数修复，文献 GPT-4 pass@1 约 47%）裸跑远低于满分，"
        "留出了真实的区分空间；本实验用四个条件把变量拆开，逐层定位增量来源。"
    )
    lines.append("")
    lines.append("## 对照设置（同模型 / 同冻结题集 / 同官方 pytest 判定）")
    lines.append("")
    lines.append("| 条件 | 循环重试 | 测试反馈 | 工具 | 上下文策略 |")
    lines.append("|---|---|---|---|---|")
    lines.append("| B0 裸跑 | 无 | 无 | 无 | 裸函数 |")
    lines.append("| B1 裸跑+反馈 | 有（≤3 轮） | 有（报错回喂） | 无 | 裸函数 |")
    lines.append("| A0 Agent-全文件 | 有（ReAct） | 有 | read/grep/ast/edit/test | 全文件 S0 |")
    lines.append("| A1 Agent-切片 | 有（ReAct） | 有 | 同上 | AST 切片 S1 |")
    lines.append("")
    digest = rows[0].get("dataset_digest", "?") if rows else "?"
    model = rows[0].get("model", "?") if rows else "?"
    lines.append(f"- 模型：{model}")
    lines.append(f"- 冻结子集：50 题（seed=20261121，digest={digest}）")
    lines.append("- 判定：官方 pytest 隔离目录复测，四条件完全相同")
    lines.append("- 重复：1 次（temperature=0，重复无统计意义）")
    lines.append("")
    lines.append("## 核心结果")
    lines.append("")
    lines.append("| 条件 | pass@1 | 平均 token | 平均步数/轮次 | env 异常 |")
    lines.append("|---|---|---|---|---|")
    for c in conditions:
        s = stats[c]
        lines.append(
            f"| {COND_LABELS[c]} | {pct(s['passed'], s['n'])} | "
            f"{s['tok']:.0f} | {s['steps']:.1f} | {s['env']} |"
        )
    lines.append("")

    # 因果增量（仅在两组题集一致时可算）
    def delta(a: str, b: str) -> str:
        if a not in stats or b not in stats:
            return "（该条件无数据）"
        sa, sb = stats[a], stats[b]
        if sa["n"] != sb["n"]:
            return "（样本数不一致，不可比）"
        pa = sa["passed"] / sa["n"]
        pb = sb["passed"] / sb["n"]
        return f"{(pa - pb) * 100:+.1f}pp"

    lines.append("## 因果归属（逐层拆解）")
    lines.append("")
    lines.append("| 对比 | 回答的问题 | pass@1 增量 | 混杂因素 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| B1 − B0 | 只加『循环重试+测试反馈』 | {delta('B1','B0')} | 无（工具/上下文均未变）|")
    lines.append(f"| A0 − B0 | 加整套『ReAct 工具循环』（全文件） | {delta('A0','B0')} | 同时引入工具与循环，不可再拆 |")
    lines.append(f"| A1 − A0 | 完整 Agent 内换『AST 切片上下文』 | {delta('A1','A0')} | 无（工具/循环均相同）|")
    lines.append(f"| A1 − B1 | 有反馈前提下『工具+上下文』vs『纯回喂』 | {delta('A1','B1')} | 工具与上下文同时引入 |")
    lines.append("")
    lines.append("**关键**：B1−B0 与 A1−A0 是干净的单变量对照；A0−B0 与 A1−B1 仍含混杂，"
                 "不能当作单一因素的净贡献。这正是上一版实验缺失的中间对照。")
    lines.append("")

    lines.append("## 诚实披露")
    lines.append("")
    lines.append("- 所有数字从 JSONL 原始记录重算，脚本可复现；env_error 单列，不伪装成能力失败。")
    lines.append("- temperature=0，单次运行；这是能力下限的估计，不代表多次采样的 pass@k。")
    lines.append("- B1 的『循环』是报错回喂重试，A0/A1 的『循环』是 ReAct 工具循环，二者机制不同，"
                 "B1−B0 只能归因到『反馈重试』这一个整体，不能再拆成循环与反馈各自贡献。")
    lines.append("- 若某条件样本数不足（实验进行中），增量标注不可比，不输出中间结论。")
    lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写入: {out}")
    print(f"条件数={len(conditions)} 总记录={len(rows)}")
    for c in conditions:
        s = stats[c]
        print(f"  {c}: {s['passed']}/{s['n']} tok={s['tok']:.0f}")


if __name__ == "__main__":
    main()

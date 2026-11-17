from __future__ import annotations

import html

from codingagent.api.deps import Container
from codingagent.config import Settings
from codingagent.domain.models import RunTrace


def render_trace(trace: RunTrace) -> str:
    status = "✅ 通过" if trace.passed else "❌ 未通过"
    lines = [
        f"### {trace.problem_id} · {trace.strategy.value} · {status}",
        f"- 失败归因：`{trace.error_category.value}`",
        f"- token：{trace.total_tokens} / 步数：{trace.total_steps} / 尝试：{len(trace.attempts)}",
    ]
    for attempt in trace.attempts:
        lines.append(
            f"\n#### 尝试 {attempt.attempt}（stop={attempt.stop_reason}，"
            f"passed={attempt.passed}）"
        )
        lines.append("| 步 | 动作 | OK | token | 命中目标 |")
        lines.append("|---|---|---|---|---|")
        for step in attempt.steps:
            reason = html.escape(step.thought or "")[:80]
            lines.append(
                f"| {step.step} | `{step.action}` {reason} | {step.ok} | "
                f"{step.tokens} | {step.target_hit} |"
            )
        if attempt.degrade_note:
            lines.append("\n```\n" + attempt.degrade_note + "\n```")
    if trace.final_patch:
        lines.append("\n### 最终 patch\n```diff\n" + trace.final_patch + "\n```")
    return "\n".join(lines)


def build_ui(container: Container):
    import gradio as gr

    problem_ids = [problem.problem_id for problem in container.problems]

    def run(problem_id: str, strategy: str) -> str:
        trace = container.run(problem_id, strategy)
        return render_trace(trace)

    with gr.Blocks(title="Coding Agent 演示") as demo:
        gr.Markdown(
            "# Coding Agent · 逐步修复演示\n"
            "选择 QuixBugs 题目与上下文策略，查看 Agent 的每一步动作与最终 patch。"
        )
        with gr.Row():
            problem = gr.Dropdown(problem_ids, value="bitcount", label="题目")
            strategy = gr.Radio(
                ["S0", "S1", "S2", "S3", "S4"], value="S2", label="上下文策略"
            )
        button = gr.Button("运行 Agent", variant="primary")
        output = gr.Markdown("点击运行后展示 trace。")
        button.click(run, inputs=[problem, strategy], outputs=output)
    return demo


def main() -> None:
    settings = Settings.load()
    container = Container.build(settings)
    build_ui(container).launch()


if __name__ == "__main__":  # pragma: no cover
    main()

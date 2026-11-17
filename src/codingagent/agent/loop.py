from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Mapping

from codingagent.config import Settings
from codingagent.context.base import BuildContext, ContextBuilder
from codingagent.correction.classifier import classify_patch_state, classify_test_report
from codingagent.correction.feedback import FeedbackBuilder
from codingagent.correction.policy import CorrectionPolicy
from codingagent.domain.enums import STRATEGY_LABELS, ActionType, ErrorCategory, FeedbackStyle, Strategy
from codingagent.domain.models import (
    AttemptRecord,
    ChatMessage,
    Classification,
    Problem,
    RunTrace,
    StepRecord,
)
from codingagent.llm.base import LLMClient, LLMError
from codingagent.sandbox.grading import PatchGrader
from codingagent.sandbox.runner import TestRunner
from codingagent.sandbox.workspace import Workspace
from codingagent.tools.base import ToolContext, ToolRegistry

from .guards import LoopGuard, TokenBudget
from .parser import parse_action
from .prompts import (
    PROMPT_VERSION,
    build_system_prompt,
    build_user_prompt,
    format_error_message,
)

_DEGRADED_STOPS = {
    "max_steps",
    "token_budget",
    "loop_detected",
    "format_errors",
    "llm_error",
}


class AgentRunner:
    """手写 ReAct 循环 + 自纠正。策略只换 context_builder，循环本身不动。"""

    def __init__(
        self,
        *,
        llm: LLMClient,
        settings: Settings,
        builder: ContextBuilder,
        tools: ToolRegistry,
        support_files: Mapping[str, str],
    ) -> None:
        self._llm = llm
        self._settings = settings
        self._builder = builder
        self._tools = tools
        self._support_files = dict(support_files)
        self._feedback = FeedbackBuilder()

    # ------------------------------------------------------------------ 入口
    def run(
        self,
        problem: Problem,
        strategy: Strategy | str,
        *,
        repeat_index: int = 0,
        config_hash: str = "",
        run_id: str | None = None,
    ) -> RunTrace:
        strategy = Strategy(str(strategy))
        style = FeedbackStyle(self._settings.correction.style_for(strategy.value))
        policy = CorrectionPolicy(self._settings.correction.max_retries, style)

        workspace = Workspace({problem.entry_path: problem.buggy_code})
        runner = TestRunner(problem, self._support_files, self._settings.sandbox)
        grader = PatchGrader(problem, runner)

        started_at = datetime.now(timezone.utc)
        attempts: list[AttemptRecord] = []
        context_meta: list[dict] = []
        feedback_text: str | None = None
        max_attempts = 1 + (0 if style == FeedbackStyle.NONE else self._settings.correction.max_retries)

        for attempt_index in range(max_attempts):
            attempt, meta = self._run_attempt(
                problem=problem,
                workspace=workspace,
                grader=grader,
                attempt_number=attempt_index + 1,
                feedback_text=feedback_text,
                style=style,
            )
            attempts.append(attempt)
            context_meta.append(meta)
            if attempt.passed:
                break
            if not policy.should_retry(attempt.classification, attempt_index):
                break
            feedback_text = self._build_feedback(style, problem, workspace, attempt)

        passed = any(attempt.passed for attempt in attempts)
        error_category = (
            ErrorCategory.NONE if passed else attempts[-1].classification.category
        )
        total_tokens = sum(attempt.tokens for attempt in attempts)
        total_steps = sum(attempt.steps_used for attempt in attempts)

        return RunTrace(
            run_id=run_id or uuid.uuid4().hex,
            strategy=strategy,
            problem_id=problem.problem_id,
            repeat_index=repeat_index,
            model=getattr(self._llm, "model", ""),
            attempts=attempts,
            passed=passed,
            error_category=error_category,
            total_tokens=total_tokens,
            total_steps=total_steps,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            config_hash=config_hash,
            prompt_version=PROMPT_VERSION,
            meta={
                "feedback_style": style.value,
                "attempts": len(attempts),
                "stop_reasons": [attempt.stop_reason for attempt in attempts],
                "final_stop_reason": attempts[-1].stop_reason,
                "contexts": context_meta,
                "degrade_notes": [
                    attempt.degrade_note for attempt in attempts if attempt.degrade_note
                ],
            },
        )

    # ------------------------------------------------------------------ 单次尝试
    def _run_attempt(
        self,
        *,
        problem: Problem,
        workspace: Workspace,
        grader: PatchGrader,
        attempt_number: int,
        feedback_text: str | None,
        style: FeedbackStyle,
    ) -> tuple[AttemptRecord, dict]:
        agent_settings = self._settings.agent
        workspace.reset()

        bundle = self._builder.build(
            BuildContext(problem=problem, workspace=workspace, feedback_text=feedback_text),
            max_chars=agent_settings.context_max_chars,
        )
        tool_ctx = ToolContext(
            problem=problem,
            workspace=workspace,
            grader=grader,
            sandbox=self._settings.sandbox,
        )
        messages = [
            ChatMessage(
                role="system",
                content=build_system_prompt(self._tools.prompt_spec(), agent_settings),
            ),
            ChatMessage(
                role="user",
                content=build_user_prompt(
                    problem, bundle, STRATEGY_LABELS[bundle.strategy]
                ),
            ),
        ]

        guard = LoopGuard()
        budget = TokenBudget(agent_settings.token_budget)
        steps: list[StepRecord] = []
        stop_reason = "max_steps"
        format_failures = 0
        llm_error: str | None = None
        step_index = 0
        started = time.perf_counter()

        while step_index < agent_settings.max_steps:
            if budget.exceeded:
                stop_reason = "token_budget"
                break
            try:
                response = self._llm.complete(messages, json_mode=True)
            except LLMError as exc:
                stop_reason = "llm_error"
                llm_error = str(exc)
                break

            step_index += 1
            budget.charge(response.total_tokens)
            outcome = parse_action(response.text)

            if outcome.error:
                format_failures += 1
                steps.append(
                    StepRecord(
                        step=step_index,
                        action="invalid_json",
                        observation=outcome.error[: agent_settings.observation_max_chars],
                        ok=False,
                        error=outcome.error,
                        tokens=response.total_tokens,
                        latency_ms=response.latency_ms,
                    )
                )
                messages.append(ChatMessage(role="assistant", content=response.text))
                messages.append(
                    ChatMessage(
                        role="user",
                        content=format_error_message(
                            outcome.error, [item.value for item in ActionType]
                        ),
                    )
                )
                if format_failures > agent_settings.max_format_retries:
                    stop_reason = "format_errors"
                    break
                continue

            action = outcome.action
            assert action is not None
            messages.append(ChatMessage(role="assistant", content=response.text))

            if action.action == ActionType.FINISH:
                steps.append(
                    StepRecord(
                        step=step_index,
                        thought=action.reason,
                        action=action.action.value,
                        args=action.args,
                        observation="agent 主动结束（finish）",
                        ok=True,
                        tokens=response.total_tokens,
                        latency_ms=response.latency_ms,
                    )
                )
                stop_reason = "finish"
                break

            result = self._tools.invoke(action.action.value, tool_ctx, action.args)
            observation = result.output[: agent_settings.observation_max_chars]
            steps.append(
                StepRecord(
                    step=step_index,
                    thought=action.reason,
                    action=action.action.value,
                    args=action.args,
                    observation=observation,
                    ok=result.ok,
                    error=result.error_category.value if result.error_category else None,
                    tokens=response.total_tokens,
                    latency_ms=response.latency_ms,
                    target_hit=bool(result.data.get("target_hit")),
                )
            )
            messages.append(
                ChatMessage(
                    role="user",
                    content=(
                        f"OBSERVATION ({action.action.value}):\n{observation}\n\n"
                        "Next action as a single JSON object."
                    ),
                )
            )

            if guard.observe(action):
                stop_reason = "loop_detected"
                break
            if action.action == ActionType.RUN_TEST and result.ok:
                stop_reason = "tests_passed"
                break

        duration_ms = int((time.perf_counter() - started) * 1000)

        # 评测端独立复算：以测试结果判定，不以模型自称或 patch 文本判定
        report = grader.grade_workspace(workspace)
        classification = classify_test_report(report)
        patch_state = classify_patch_state(workspace)
        passed = report.ok and patch_state is None
        if passed:
            classification = Classification(category=ErrorCategory.NONE)
        elif patch_state is not None:
            classification = patch_state
        if llm_error:
            classification = Classification(
                category=ErrorCategory.ENV_ERROR,
                retryable=False,
                excerpt=llm_error,
            )

        degraded = (stop_reason in _DEGRADED_STOPS or llm_error is not None) and not passed
        degrade_note = (
            self._degrade_note(stop_reason, workspace, classification, steps)
            if degraded
            else None
        )

        patch_text = workspace.unified_diff()
        record = AttemptRecord(
            attempt=attempt_number,
            steps=steps,
            patch_text=patch_text,
            changed_files=[patch.path for patch in workspace.patches()],
            test_report=report,
            classification=classification,
            passed=passed,
            tokens=budget.spent,
            steps_used=step_index,
            duration_ms=duration_ms,
            stop_reason=stop_reason,
            degraded=degraded,
            degrade_note=degrade_note,
        )
        meta = {
            "attempt": attempt_number,
            "feedback_style": style.value,
            "stop_reason": stop_reason,
            "context": bundle.meta,
            "context_chars": len(bundle.text),
            "token_estimate": bundle.token_estimate,
        }
        return record, meta

    # ------------------------------------------------------------------ 回喂
    def _build_feedback(
        self,
        style: FeedbackStyle,
        problem: Problem,
        workspace: Workspace,
        attempt: AttemptRecord,
    ) -> str | None:
        if attempt.test_report is None or attempt.classification is None:
            return None
        return self._feedback.build(
            style.value,
            problem=problem,
            workspace=workspace,
            report=attempt.test_report,
            classification=attempt.classification,
            last_patch=attempt.patch_text,
        )

    @staticmethod
    def _degrade_note(
        stop_reason: str,
        workspace: Workspace,
        classification: Classification,
        steps: list[StepRecord],
    ) -> str:
        """兜底：给最接近成功的 patch 草稿 + 置信度说明 + 不确定的步骤。"""

        patch = workspace.unified_diff() or "（本次尝试没有产生任何 patch）"
        failed = classification.failed_test or "目标函数"
        last_step = steps[-1].action if steps else "无"
        return (
            f"本次尝试提前结束（stop_reason={stop_reason}），未确认修复。\n"
            f"最接近成功的 patch 草稿:\n```diff\n{patch}\n```\n"
            f"置信度说明: 该方案未通过官方测试，可能在 {failed} 相关逻辑处有误。\n"
            f"不确定的步骤: 最后一次工具调用是 `{last_step}`。"
        )

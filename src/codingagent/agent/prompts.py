from __future__ import annotations

from codingagent.config import AgentSettings
from codingagent.context.base import ContextBundle
from codingagent.domain.models import Problem

PROMPT_VERSION = "2026-11-17.1"

SYSTEM_PROMPT = """You are a senior Python engineer acting as an autonomous bug-fixing agent.
You fix exactly one bug in one small Python program, then prove it with the official test suite.

## Tools
{tools}

## Reply protocol
Reply with ONE JSON object and NOTHING else — no prose, no markdown fence:
{{"action": "<tool name>", "args": {{...}}, "reason": "<one short sentence>"}}

## Rules
1. Read before you edit. `read_file` output is prefixed with line numbers; those numbers are NOT part of the file content and must never appear in `edit_file.old`.
2. `edit_file.old` must be an exact substring of the current file and must occur exactly once. Keep the original indentation.
3. Make the minimal change that fixes the root cause. Do not rewrite unrelated functions.
4. Run `run_test` after editing. If tests pass, call `finish`.
5. Never repeat an identical tool call with identical arguments — it will be blocked.
6. If a tool call fails, read the failure message and adjust; do not guess blindly.

## Budget
At most {max_steps} steps and {token_budget} tokens. When the budget is exhausted the run ends
and the current patch is graded as-is, so prefer a working minimal patch over a perfect rewrite.
"""

USER_TEMPLATE = """# Task
Fix the bug in `{entry_path}` (QuixBugs problem `{problem_id}`).

{description}

# Context: {strategy_label}
{context}

Begin now: reply with your first action as a single JSON object.
"""

FORMAT_ERROR_TEMPLATE = """Your previous reply could not be parsed: {error}

Reply again with exactly one JSON object:
{{"action": "<one of: {allowed}>", "args": {{...}}, "reason": "..."}}
Do not wrap it in markdown fences and do not add commentary."""


def build_system_prompt(tools_spec: str, settings: AgentSettings) -> str:
    return SYSTEM_PROMPT.format(
        tools=tools_spec,
        max_steps=settings.max_steps,
        token_budget=settings.token_budget,
    )


def build_user_prompt(problem: Problem, bundle: ContextBundle, strategy_label: str) -> str:
    description = problem.description.strip() or "（题目未提供额外说明，请从测试推断期望行为）"
    return USER_TEMPLATE.format(
        entry_path=problem.entry_path,
        problem_id=problem.problem_id,
        description="## Problem statement\n" + description,
        strategy_label=strategy_label,
        context=bundle.text,
    )


def format_error_message(error: str, allowed_actions: list[str]) -> str:
    return FORMAT_ERROR_TEMPLATE.format(error=error, allowed=", ".join(allowed_actions))

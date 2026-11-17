from __future__ import annotations

import json
from typing import Any, Sequence

from codingagent.domain.enums import ActionType, ErrorCategory
from codingagent.domain.models import Action, ParseOutcome, Problem
from codingagent.sandbox.workspace import Workspace


def _candidate_objects(text: str) -> list[str]:
    decoder = json.JSONDecoder()
    candidates: list[str] = []
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(obj, dict):
            candidates.append(text[start : start + end])
        index = start + end
    return candidates


def parse_action(text: str) -> ParseOutcome:
    """从模型输出里抽出唯一动作并做 schema 校验；失败只返回原因，不抛异常。"""

    if not text or not text.strip():
        return ParseOutcome(error="输出为空：必须返回一个 JSON 对象。", raw=text)

    candidates = _candidate_objects(text)
    if not candidates:
        return ParseOutcome(
            error="未找到合法的 JSON 对象。只输出一个 JSON 对象，不要写解释文字或 Markdown。",
            raw=text,
        )

    payload: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "action" in data:
            payload = data
            break
    if payload is None:
        return ParseOutcome(
            error="JSON 里缺少 action 字段。必须形如 "
            '{"action": "read_file", "args": {"path": "..."}, "reason": "..."}',
            raw=text,
        )

    allowed = [item.value for item in ActionType]
    action_value = payload.get("action")
    if not isinstance(action_value, str) or action_value not in allowed:
        return ParseOutcome(
            error=f"非法 action: {action_value!r}。只允许 {allowed}。",
            raw=text,
        )

    args = payload.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return ParseOutcome(error="args 必须是 JSON 对象。", raw=text)

    reason = payload.get("reason", "")
    return ParseOutcome(
        action=Action(action=ActionType(action_value), args=args, reason=str(reason or "")),
        raw=text,
    )


ALLOWED_RETRYABLE = {category.value for category in ErrorCategory if category.retryable}


def describe_problem(problem: Problem) -> str:
    """把题目描述拼成稳定的任务陈述（所有策略共用）。"""

    lines = [f"题目 `{problem.problem_id}`，入口文件 `{problem.entry_path}`。"]
    if problem.description:
        lines.append("题目说明（含输入/输出语义）：")
        lines.append("```\n" + problem.description + "\n```")
    return "\n".join(lines)


def crc_hint(workspace: Workspace) -> str:
    """当前工作区里已存在的文件清单，帮助模型避免猜错路径。"""

    return json.dumps(sorted(workspace.paths), ensure_ascii=False)


def join_messages(messages: Sequence[Any]) -> str:  # pragma: no cover - 调试辅助
    return "\n".join(f"[{m.role}] {m.content}" for m in messages)

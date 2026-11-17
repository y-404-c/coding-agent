from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from codingagent.domain.enums import Strategy
from codingagent.domain.models import Problem
from codingagent.sandbox.workspace import Workspace

TRUNCATION_NOTE = "\n\n...[上下文超过预算，已截断]"


@dataclass
class BuildContext:
    problem: Problem
    workspace: Workspace
    feedback_text: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ContextBundle(BaseModel):
    text: str
    strategy: Strategy
    sections: dict[str, str] = Field(default_factory=dict)
    token_estimate: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)


class ContextBuilder:
    """策略只决定上下文构造方式，Agent Loop 不因策略改变而改动。"""

    strategy: Strategy = Strategy.S0

    def build(self, ctx: BuildContext, *, max_chars: int = 12_000) -> ContextBundle:
        sections = self.sections(ctx)
        if ctx.feedback_text:
            sections["上一次尝试的反馈"] = ctx.feedback_text
        chunks = []
        for title, body in sections.items():
            if not body:
                continue
            chunks.append(f"### {title}\n{body}")
        text = "\n\n".join(chunks).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + TRUNCATION_NOTE
        return ContextBundle(
            text=text,
            strategy=self.strategy,
            sections=dict(sections),
            token_estimate=len(text) // 4,
            meta=self.meta(ctx),
        )

    def sections(self, ctx: BuildContext) -> OrderedDict[str, str]:  # pragma: no cover
        raise NotImplementedError

    def meta(self, ctx: BuildContext) -> dict[str, Any]:
        return {"strategy": self.strategy.value}

    # ---------------------------------------------------------------- 工具方法
    @staticmethod
    def _fence(code: str, path: str) -> str:
        return f"文件 `{path}`：\n```python\n{code.rstrip()}\n```"

    def _source(self, ctx: BuildContext) -> str:
        path = ctx.problem.entry_path
        return ctx.workspace.read(path)

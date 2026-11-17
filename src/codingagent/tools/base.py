from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Iterable, Mapping

from codingagent.config import SandboxSettings
from codingagent.domain.enums import ActionType, ErrorCategory
from codingagent.domain.models import Problem, ToolResult
from codingagent.sandbox.grading import PatchGrader
from codingagent.sandbox.workspace import Workspace


@dataclass
class ToolContext:
    problem: Problem
    workspace: Workspace
    grader: PatchGrader
    sandbox: SandboxSettings
    history: list[str] = field(default_factory=list)


class BaseTool(ABC):
    """每个工具都必须返回明确的失败结果，而不是抛异常。"""

    name: ClassVar[str]
    description: ClassVar[str]
    arg_schema: ClassVar[dict[str, str]]

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "args": self.arg_schema,
        }

    def describe(self) -> str:
        args = ", ".join(f"{key}: {value}" for key, value in self.arg_schema.items())
        return f"- `{self.name}`（{args}）: {self.description}"

    def invoke(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolResult:
        try:
            return self._run(ctx, args)
        except Exception as exc:  # 兜底：工具 bug 不能把 Agent 打崩
            return ToolResult(
                ok=False,
                output=f"工具内部异常（已捕获，不中断循环）: {type(exc).__name__}: {exc}",
                error_category=ErrorCategory.UNKNOWN,
            )

    @abstractmethod
    def _run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolResult: ...

    @staticmethod
    def _fail(message: str, category: ErrorCategory | None = ErrorCategory.UNKNOWN) -> ToolResult:
        return ToolResult(ok=False, output=message, error_category=category)


class ToolRegistry:
    def __init__(self, tools: Iterable[BaseTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def invoke(self, name: str, ctx: ToolContext, args: Mapping[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                ok=False,
                output=f"未知工具 `{name}`。可用工具: {self.names}",
                error_category=ErrorCategory.UNKNOWN,
            )
        if not isinstance(args, Mapping):
            return ToolResult(
                ok=False,
                output="args 必须是 JSON 对象。",
                error_category=ErrorCategory.UNKNOWN,
            )
        return tool.invoke(ctx, args)

    def prompt_spec(self) -> str:
        lines = [tool.describe() for tool in self._tools.values()]
        lines.append(
            "- `finish`（无参数）: 结束本次尝试。测试通过后再调用；不要用它代替修改代码。"
        )
        return "\n".join(lines)

    def schema_json(self) -> str:
        return json.dumps(
            {name: tool.spec() for name, tool in self._tools.items()},
            ensure_ascii=False,
            indent=2,
        )


def build_default_registry() -> ToolRegistry:
    from .ast_slice import AstSliceTool
    from .edit_file import EditFileTool
    from .grep import GrepTool
    from .read_file import ReadFileTool
    from .run_test import RunTestTool

    return ToolRegistry(
        [
            ReadFileTool(),
            GrepTool(),
            AstSliceTool(),
            EditFileTool(),
            RunTestTool(),
        ]
    )


__all__ = ["ActionType", "BaseTool", "ToolContext", "ToolRegistry", "build_default_registry"]

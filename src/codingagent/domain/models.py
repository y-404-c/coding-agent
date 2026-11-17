from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .enums import ActionType, ErrorCategory, Strategy


class Problem(BaseModel):
    """一道 QuixBugs 题目的全部静态信息（自包含，不依赖磁盘布局）。"""

    problem_id: str
    function_name: str
    entry_path: str
    test_path: str
    buggy_code: str
    correct_code: str
    test_code: str
    test_json: str | None = None
    description: str = ""

    @property
    def line_count(self) -> int:
        return len(self.buggy_code.splitlines())


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMResponse(BaseModel):
    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class Action(BaseModel):
    """模型输出的单个动作。args 由具体工具自行校验，非法参数走失败返回。"""

    action: ActionType
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ParseOutcome(BaseModel):
    action: Action | None = None
    error: str | None = None
    raw: str = ""


class ToolResult(BaseModel):
    ok: bool
    output: str
    error_category: ErrorCategory | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class EditRecord(BaseModel):
    path: str
    old: str
    new: str


class StepRecord(BaseModel):
    """一条 trace：thought / action / observation / 耗时 / token 全记录。"""

    step: int
    thought: str = ""
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    observation: str = ""
    ok: bool = True
    error: str | None = None
    tokens: int = 0
    latency_ms: int = 0
    target_hit: bool = False


class TestReport(BaseModel):
    __test__ = False  # 防止 pytest 把领域模型当测试类收集

    returncode: int = 0
    timed_out: bool = False
    duration_ms: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failed_tests: list[str] = Field(default_factory=list)
    error_types: list[str] = Field(default_factory=list)
    assertion_lines: list[str] = Field(default_factory=list)
    output_tail: str = ""
    command: list[str] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors

    @property
    def ok(self) -> bool:
        """判定物：官方测试全绿。不是 patch 文本相似度。"""

        if self.timed_out or self.returncode != 0:
            return False
        return self.failed == 0 and self.errors == 0 and self.passed > 0


class Classification(BaseModel):
    category: ErrorCategory
    retryable: bool = False
    failed_test: str | None = None
    expected: str | None = None
    actual: str | None = None
    exception_type: str | None = None
    excerpt: str = ""


class AttemptRecord(BaseModel):
    attempt: int
    steps: list[StepRecord] = Field(default_factory=list)
    patch_text: str = ""
    changed_files: list[str] = Field(default_factory=list)
    test_report: TestReport | None = None
    classification: Classification | None = None
    passed: bool = False
    tokens: int = 0
    steps_used: int = 0
    duration_ms: int = 0
    stop_reason: str = ""
    degraded: bool = False
    degrade_note: str | None = None


class RunTrace(BaseModel):
    run_id: str
    strategy: Strategy
    problem_id: str
    repeat_index: int = 0
    model: str = ""
    attempts: list[AttemptRecord] = Field(default_factory=list)
    passed: bool = False
    error_category: ErrorCategory = ErrorCategory.NONE
    total_tokens: int = 0
    total_steps: int = 0
    started_at: datetime
    finished_at: datetime | None = None
    config_hash: str = ""
    prompt_version: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def final_patch(self) -> str:
        for attempt in reversed(self.attempts):
            if attempt.patch_text:
                return attempt.patch_text
        return ""

    def pass_at(self, k: int) -> bool:
        return any(attempt.passed for attempt in self.attempts[:k])

    @property
    def localization_step(self) -> int | None:
        """第一次触达目标文件/函数的工具调用序号（1 起）。"""

        for step in self.attempts[0].steps if self.attempts else []:
            if step.target_hit:
                return step.step
        return None

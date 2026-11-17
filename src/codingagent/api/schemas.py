from __future__ import annotations

from pydantic import BaseModel, Field

from codingagent.domain.enums import Strategy


class RunRequest(BaseModel):
    problem_id: str = Field(..., description="QuixBugs 题目 id，例如 bitcount")
    strategy: Strategy = Strategy.S4
    repeat_index: int = 0


class ProblemSummary(BaseModel):
    problem_id: str
    function_name: str
    entry_path: str
    line_count: int
    description: str


class ProblemDetail(ProblemSummary):
    buggy_code: str
    test_code: str


class RunSummary(BaseModel):
    run_id: str
    strategy: str
    problem_id: str
    passed: bool
    error_category: str
    total_tokens: int
    total_steps: int
    attempts: int
    stop_reason: str


class HealthResponse(BaseModel):
    status: str
    model: str
    dataset_problems: int
    runs_total: int

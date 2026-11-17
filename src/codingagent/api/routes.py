from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from codingagent.api.schemas import (
    HealthResponse,
    ProblemDetail,
    ProblemSummary,
    RunRequest,
    RunSummary,
)
from codingagent.domain.enums import Strategy
from codingagent.domain.models import RunTrace
from codingagent.eval.metrics import compute_metrics

router = APIRouter()


def _container(request: Request):
    return request.app.state.container


def _summary(trace: RunTrace) -> RunSummary:
    return RunSummary(
        run_id=trace.run_id,
        strategy=trace.strategy.value,
        problem_id=trace.problem_id,
        passed=trace.passed,
        error_category=trace.error_category.value,
        total_tokens=trace.total_tokens,
        total_steps=trace.total_steps,
        attempts=len(trace.attempts),
        stop_reason=str(trace.meta.get("final_stop_reason", "")),
    )


@router.get("/healthz", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    container = _container(request)
    counts = container.repository.counts()
    return HealthResponse(
        status="ok",
        model=container.settings.llm.model,
        dataset_problems=len(container.problems),
        runs_total=counts["total"],
    )


@router.get("/problems", response_model=list[ProblemSummary])
def list_problems(request: Request) -> list[ProblemSummary]:
    container = _container(request)
    return [
        ProblemSummary(
            problem_id=problem.problem_id,
            function_name=problem.function_name,
            entry_path=problem.entry_path,
            line_count=problem.line_count,
            description=problem.description,
        )
        for problem in container.problems
    ]


@router.get("/problems/{problem_id}", response_model=ProblemDetail)
def get_problem(problem_id: str, request: Request) -> ProblemDetail:
    container = _container(request)
    try:
        problem = container.problem(problem_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProblemDetail(
        problem_id=problem.problem_id,
        function_name=problem.function_name,
        entry_path=problem.entry_path,
        line_count=problem.line_count,
        description=problem.description,
        buggy_code=problem.buggy_code,
        test_code=problem.test_code,
    )


@router.post("/runs")
def create_run(payload: RunRequest, request: Request) -> dict:
    container = _container(request)
    try:
        trace = container.run(payload.problem_id, Strategy(payload.strategy), payload.repeat_index)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return trace.model_dump(mode="json")


@router.get("/runs")
def list_runs(
    request: Request, strategy: str | None = None, limit: int = 20
) -> list[RunSummary]:
    container = _container(request)
    traces = container.repository.list_traces(strategy=strategy, limit=limit)
    return [_summary(trace) for trace in traces]


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    container = _container(request)
    trace = container.repository.get(run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return trace.model_dump(mode="json")


@router.get("/metrics")
def metrics(request: Request, strategy: str | None = None) -> dict:
    container = _container(request)
    traces = container.repository.list_traces(strategy=strategy)
    computed = compute_metrics(traces)
    return {
        name: {
            "runs": item.runs,
            "pass_at_1": item.pass_at_1,
            "pass_at_2": item.pass_at_2,
            "avg_tokens": item.avg_tokens,
            "avg_steps": item.avg_steps,
            "localization_top1": item.localization_top1,
            "localization_top3": item.localization_top3,
            "failure_distribution": item.failure_distribution,
        }
        for name, item in computed.items()
    }

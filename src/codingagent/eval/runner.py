from __future__ import annotations

import hashlib
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from codingagent.agent.loop import AgentRunner
from codingagent.agent.prompts import PROMPT_VERSION
from codingagent.config import Settings
from codingagent.context import build_context_builder
from codingagent.dataset import QuixBugsDataset
from codingagent.domain.enums import ErrorCategory, Strategy
from codingagent.domain.models import AttemptRecord, Classification, Problem, RunTrace, TestReport
from codingagent.llm.base import LLMClient
from codingagent.storage import TraceRepository
from codingagent.tools import build_default_registry


class ExperimentRunner:
    """策略 × 题集 × 复现次数的受控实验矩阵。

    每个 (策略, 题目, 复现) 组合立即落库，中断后可 --resume 续跑。
    """

    def __init__(
        self,
        settings: Settings,
        dataset: QuixBugsDataset,
        llm: LLMClient,
        *,
        repository: TraceRepository | None = None,
        progress: Callable[[str], None] = print,
    ) -> None:
        self.settings = settings
        self.dataset = dataset
        self.llm = llm
        self.repository = repository or TraceRepository(settings.eval.db_path)
        self.progress = progress
        self._support_files = dataset.support_files()
        self._tools = build_default_registry()
        self._builders = {
            strategy: build_context_builder(strategy, dataset.load_all())
            for strategy in (Strategy(value) for value in settings.eval.strategies)
        }

    # ------------------------------------------------------------------ 指纹
    def config_hash(self) -> str:
        payload = {
            "settings": self.settings.as_hashable(),
            "prompt_version": PROMPT_VERSION,
            "dataset": self.dataset.manifest()["digest"],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    # ------------------------------------------------------------------ 执行
    def run(
        self,
        *,
        strategies: list[str],
        problem_ids: list[str],
        repeats: int,
        resume: bool = True,
    ) -> list[RunTrace]:
        config_hash = self.config_hash()
        traces: list[RunTrace] = []
        total = len(strategies) * len(problem_ids) * repeats
        done = 0
        for repeat_index in range(repeats):
            for strategy_value in strategies:
                strategy = Strategy(str(strategy_value))
                builder = self._builders.get(strategy) or build_context_builder(
                    strategy, self.dataset.load_all()
                )
                runner = AgentRunner(
                    llm=self.llm,
                    settings=self.settings,
                    builder=builder,
                    tools=self._tools,
                    support_files=self._support_files,
                )
                for problem_id in problem_ids:
                    done += 1
                    if resume and self.repository.exists(
                        strategy.value, problem_id, repeat_index, config_hash
                    ):
                        self.progress(
                            f"[{done}/{total}] skip  {strategy.value} {problem_id} rep={repeat_index}"
                        )
                        continue
                    problem = self.dataset.load(problem_id)
                    try:
                        trace = runner.run(
                            problem,
                            strategy,
                            repeat_index=repeat_index,
                            config_hash=config_hash,
                        )
                    except Exception as exc:  # 单题故障不能打断整个矩阵
                        trace = self._error_trace(
                            problem, strategy, repeat_index, config_hash, exc
                        )
                    self.repository.save(trace)
                    traces.append(trace)
                    self.progress(
                        f"[{done}/{total}] {strategy.value} {problem_id} rep={repeat_index} "
                        f"pass={trace.passed} tokens={trace.total_tokens} "
                        f"cat={trace.error_category.value}"
                    )
        return traces

    def _error_trace(
        self,
        problem: Problem,
        strategy: Strategy,
        repeat_index: int,
        config_hash: str,
        exc: Exception,
    ) -> RunTrace:
        now = datetime.now(timezone.utc)
        note = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        attempt = AttemptRecord(
            attempt=1,
            passed=False,
            classification=Classification(
                category=ErrorCategory.ENV_ERROR, retryable=False, excerpt=note
            ),
            test_report=TestReport(returncode=-1, output_tail=note),
            stop_reason="infra_error",
            degraded=True,
            degrade_note=f"评测基础设施异常，该题按失败记录（未静默丢弃）：{note}",
        )
        return RunTrace(
            run_id=f"infra-{problem.problem_id}-{strategy.value}-{repeat_index}-{now.timestamp()}",
            strategy=strategy,
            problem_id=problem.problem_id,
            repeat_index=repeat_index,
            model=getattr(self.llm, "model", ""),
            attempts=[attempt],
            passed=False,
            error_category=ErrorCategory.ENV_ERROR,
            started_at=now,
            finished_at=now,
            config_hash=config_hash,
            prompt_version=PROMPT_VERSION,
            meta={"infra_error": note},
        )


def default_problem_ids(settings: Settings, dataset: QuixBugsDataset) -> list[str]:
    return dataset.problem_ids()


def reports_dir(settings: Settings) -> Path:
    path = Path(settings.eval.reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path

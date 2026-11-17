from __future__ import annotations

from dataclasses import dataclass, field

from codingagent.agent.loop import AgentRunner
from codingagent.config import Settings
from codingagent.context import build_context_builder
from codingagent.context.base import ContextBuilder
from codingagent.dataset import QuixBugsDataset
from codingagent.domain.enums import Strategy
from codingagent.domain.models import Problem, RunTrace
from codingagent.llm import LLMClient, build_llm
from codingagent.storage import TraceRepository
from codingagent.tools import ToolRegistry, build_default_registry


@dataclass
class Container:
    """进程级依赖容器：数据集、LLM、策略构造器只初始化一次。"""

    settings: Settings
    dataset: QuixBugsDataset
    repository: TraceRepository
    tools: ToolRegistry
    support_files: dict[str, str]
    problems: list[Problem]
    builders: dict[Strategy, ContextBuilder] = field(default_factory=dict)
    _llm: LLMClient | None = None

    @classmethod
    def build(cls, settings: Settings) -> Container:
        dataset = QuixBugsDataset(
            settings.dataset.root,
            source_url=settings.dataset.source_url,
            auto_fetch=settings.dataset.auto_fetch,
        )
        problems = dataset.load_all()
        return cls(
            settings=settings,
            dataset=dataset,
            repository=TraceRepository(settings.eval.db_path),
            tools=build_default_registry(),
            support_files=dataset.support_files(),
            problems=problems,
        )

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = build_llm(self.settings.llm)
        return self._llm

    def builder(self, strategy: Strategy) -> ContextBuilder:
        if strategy not in self.builders:
            self.builders[strategy] = build_context_builder(strategy, self.problems)
        return self.builders[strategy]

    def problem(self, problem_id: str) -> Problem:
        return self.dataset.load(problem_id)

    def run(self, problem_id: str, strategy: Strategy, repeat_index: int = 0) -> RunTrace:
        runner = AgentRunner(
            llm=self.llm,
            settings=self.settings,
            builder=self.builder(strategy),
            tools=self.tools,
            support_files=self.support_files,
        )
        trace = runner.run(
            self.problem(problem_id),
            strategy,
            repeat_index=repeat_index,
            config_hash="api",
        )
        self.repository.save(trace)
        return trace

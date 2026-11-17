from __future__ import annotations

from codingagent.context.base import BuildContext, ContextBuilder, ContextBundle
from codingagent.context.fewshot import FewShotPool, FewShotSelector
from codingagent.context.strategies import (
    AstSliceContextBuilder,
    AstSliceWithTestsContextBuilder,
    CorrectiveContextBuilder,
    FewShotContextBuilder,
    FullFileContextBuilder,
)
from codingagent.domain.enums import Strategy
from codingagent.domain.models import Problem


def build_context_builder(strategy: Strategy, pool: list[Problem]) -> ContextBuilder:
    fewshot = FewShotPool(pool, k=3)
    mapping: dict[Strategy, ContextBuilder] = {
        Strategy.S0: FullFileContextBuilder(),
        Strategy.S1: AstSliceContextBuilder(),
        Strategy.S2: AstSliceWithTestsContextBuilder(),
        Strategy.S3: FewShotContextBuilder(fewshot),
        Strategy.S4: CorrectiveContextBuilder(fewshot),
    }
    return mapping[Strategy(str(strategy))]


__all__ = [
    "AstSliceContextBuilder",
    "AstSliceWithTestsContextBuilder",
    "BuildContext",
    "ContextBuilder",
    "ContextBundle",
    "CorrectiveContextBuilder",
    "FewShotContextBuilder",
    "FewShotPool",
    "FewShotSelector",
    "FullFileContextBuilder",
    "build_context_builder",
]

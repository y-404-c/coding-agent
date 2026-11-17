from __future__ import annotations

from collections import OrderedDict

from codingagent.context.slicing import (
    doc_tokens,
    function_arity,
    identifier_tokens,
    slice_function,
)
from codingagent.domain.models import Problem


class FewShotSelector:
    """从**其他题目**的官方正确版本里挑相似修复范例（绝不使用本题答案）。"""

    def __init__(self, pool: list[Problem], k: int = 3) -> None:
        self.k = k
        self._pool = [
            {
                "problem": problem,
                "identifiers": identifier_tokens(problem.correct_code),
                "doc_tokens": doc_tokens(problem.description),
                "arity": function_arity(problem.buggy_code, problem.function_name),
                "buggy_slice": slice_function(problem.buggy_code, problem.function_name),
                "correct_slice": slice_function(problem.correct_code, problem.function_name),
            }
            for problem in pool
        ]

    def select(self, problem: Problem) -> list[dict[str, object]]:
        target_identifiers = identifier_tokens(problem.buggy_code)
        target_doc = doc_tokens(problem.description)
        target_arity = function_arity(problem.buggy_code, problem.function_name)

        scored = []
        for item in self._pool:
            if item["problem"].problem_id == problem.problem_id:
                continue
            score = self._similarity(
                target_identifiers,
                target_doc,
                target_arity,
                item["identifiers"],
                item["doc_tokens"],
                item["arity"],
            )
            scored.append((score, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        selected = []
        for score, item in scored[: self.k]:
            if not item["buggy_slice"].found or not item["correct_slice"].found:
                continue
            selected.append(
                {
                    "problem_id": item["problem"].problem_id,
                    "score": round(score, 3),
                    "buggy": item["buggy_slice"].code,
                    "correct": item["correct_slice"].code,
                }
            )
        return selected

    @staticmethod
    def _similarity(
        ids_a: set[str],
        doc_a: set[str],
        arity_a: int | None,
        ids_b: set[str],
        doc_b: set[str],
        arity_b: int | None,
    ) -> float:
        def jaccard(x: set[str], y: set[str]) -> float:
            if not x or not y:
                return 0.0
            return len(x & y) / len(x | y)

        arity_bonus = 1.0 if arity_a is not None and arity_a == arity_b else 0.0
        return 0.5 * jaccard(ids_a, ids_b) + 0.3 * jaccard(doc_a, doc_b) + 0.2 * arity_bonus

    @staticmethod
    def render(examples: list[dict[str, object]]) -> str:
        if not examples:
            return ""
        blocks = ["以下范例来自题库中**其他题目**的官方正确版本，仅供参照修复风格："]
        for index, example in enumerate(examples, start=1):
            blocks.append(
                "\n".join(
                    [
                        f"#### 范例 {index}：{example['problem_id']}（错误实现 → 官方正确实现）",
                        "错误：",
                        f"```python\n{example['buggy']}\n```",
                        "正确：",
                        f"```python\n{example['correct']}\n```",
                    ]
                )
            )
        return "\n\n".join(blocks)


class FewShotPool:
    """加载期缓存的选择器容器，避免每题重复构造。"""

    def __init__(self, problems: list[Problem], k: int = 3) -> None:
        self._selector = FewShotSelector(problems, k=k)

    def for_problem(self, problem: Problem) -> list[dict[str, object]]:
        return self._selector.select(problem)


__all__ = ["FewShotPool", "FewShotSelector", "OrderedDict"]

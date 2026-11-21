"""HumanEvalFix 数据集加载器（bigcode/humanevalpack 的 python fix 子集）。

与 QuixBugsDataset 同接口（problem_ids/load/load_all/support_files/manifest），
因此 ExperimentRunner 与 AgentRunner 可零改动复用。

关键差异：
- 原始 task_id 形如 ``Python/0``，这里映射为 ``hef_0`` 以避免路径分隔符。
- test 字段自带模块级 ``check(entry_point)`` 调用，必须剥掉后再包成
  ``def test_entry(): check(entry_point)``，否则 pytest 在 collection 阶段
  就把"答案错误"记成 ERROR（rc=2）而非标准的测试失败（rc=1）。
- 题目自带 example_test / docstring，是天然的"官方测试上下文"。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Iterable

from codingagent.context.slicing import top_level_symbols

from .quixbugs import DatasetError

DEFAULT_SOURCE = Path("data/raw/humanevalfix/humanevalpack_python.json")


def restore_imports(buggy_code: str, model_output: str, function_name: str) -> str:
    """把模型返回的裸函数套回原文件的导入头，避免 NameError(List/Dict/...)。

    HumanEvalFix 的 prompt 头部含 ``from typing import List`` 等导入，是函数签名
    的一部分。裸跑模型只被要求回函数定义，会丢掉这些导入，直接判定会 collection
    阶段 NameError。这里把 buggy_code 中 ``def`` 之前的前导内容（导入 + 注释）补回。
    """
    output = model_output.strip() + "\n"
    if re.search(r"\bdef\s+" + re.escape(function_name) + r"\s*\(", output):
        # 已有函数定义：补导入头
        head = buggy_code.split("def ", 1)[0]
        if "def " not in head:
            return head.rstrip() + "\n\n\n" + output
    return output


def strip_module_level_check(test: str, entry_point: str) -> str:
    """删除 test 末尾的 ``check(entry_point)`` 模块级调用，只保留函数定义。"""
    lines = test.rstrip("\n").split("\n")
    pattern = re.compile(rf"^check\({re.escape(entry_point)}\)\s*$")
    while lines and (not lines[-1].strip() or pattern.match(lines[-1])):
        lines.pop()
    return "\n".join(lines)


class HumanEvalFixDataset:
    """HumanEvalFix 164 题，支持冻结子集。"""

    def __init__(
        self,
        source: Path | str = DEFAULT_SOURCE,
        *,
        subset_size: int = 0,
        seed: int = 20261121,
    ) -> None:
        self.source = Path(source)
        self.subset_size = subset_size
        self.seed = seed
        self._rows: dict[str, dict] = {}
        self._cache: dict[str, object] = {}
        self._load_source()

    def _load_source(self) -> None:
        if not self.source.exists():
            raise DatasetError(f"缺少 HumanEvalFix 数据文件: {self.source}")
        rows = json.loads(self.source.read_text(encoding="utf-8"))
        for row in rows:
            pid = self._to_id(row["task_id"])
            self._rows[pid] = row

    @staticmethod
    def _to_id(task_id: str) -> str:
        return "hef_" + task_id.split("/")[-1]

    def problem_ids(self) -> list[str]:
        ids = sorted(self._rows, key=lambda x: int(x.split("_")[-1]))
        if self.subset_size and self.subset_size < len(ids):
            rng = random.Random(self.seed)
            ids = sorted(rng.sample(ids, self.subset_size), key=lambda x: int(x.split("_")[-1]))
        return ids

    def load(self, problem_id: str) -> object:
        from codingagent.domain.models import Problem

        if problem_id in self._cache:
            return self._cache[problem_id]  # type: ignore[return-value]
        row = self._rows.get(problem_id)
        if row is None:
            raise DatasetError(f"未知 HumanEvalFix 题目: {problem_id}")

        ep = row["entry_point"]
        buggy_code = row["prompt"] + row["buggy_solution"]
        correct_code = row["prompt"] + row["canonical_solution"]
        test_body = strip_module_level_check(
            row["import"] + "\n" + row["test_setup"] + "\n" + row["test"], ep
        )
        test_code = (
            f"from python_programs.{problem_id} import {ep}\n"
            f"{test_body}\n\n"
            f"def test_entry():\n    check({ep})\n"
        )
        symbols = top_level_symbols(buggy_code)
        function_name = ep if ep in symbols else (symbols[0] if symbols else ep)

        # description：docstring + 示例测试，作为可选的上下文信号
        description = (row.get("docstring") or "").strip()
        example = (row.get("example_test") or "").strip()

        problem = Problem(
            problem_id=problem_id,
            function_name=function_name,
            entry_path=f"python_programs/{problem_id}.py",
            test_path=f"python_testcases/test_{problem_id}.py",
            buggy_code=buggy_code,
            correct_code=correct_code,
            test_code=test_code,
            test_json=None,
            description=description,
        )
        self._cache[problem_id] = problem
        return problem

    def load_all(self, problem_ids: Iterable[str] | None = None) -> list[object]:
        ids = list(problem_ids) if problem_ids is not None else self.problem_ids()
        return [self.load(pid) for pid in ids]

    def support_files(self) -> dict[str, str]:
        # HumanEvalFix 测试自包含，无外部辅助文件
        return {}

    def manifest(self) -> dict:
        ids = self.problem_ids()
        payload = {
            "dataset": "humanevalfix",
            "source": str(self.source),
            "total": len(self._rows),
            "subset_size": self.subset_size,
            "seed": self.seed,
            "ids": ids,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        return {"name": "humanevalfix", "size": len(ids), "digest": digest, "ids": ids}

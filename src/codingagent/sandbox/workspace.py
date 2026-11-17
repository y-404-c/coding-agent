from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Mapping

from codingagent.domain.models import EditRecord


@dataclass(frozen=True)
class FilePatch:
    path: str
    before: str
    after: str


@dataclass
class EditOutcome:
    ok: bool
    message: str
    diff: str = ""
    occurrences: int = 0


class Workspace:
    """内存虚拟工作区：工具只改这里，绝不直接落盘。

    好处：一键回滚做多策略对比、patch 作为评测产物、改动范围可审计。
    """

    def __init__(self, files: Mapping[str, str]) -> None:
        self._originals: dict[str, str] = {self._norm(k): v for k, v in files.items()}
        self._files: dict[str, str] = dict(self._originals)
        self._edits: list[EditRecord] = []

    # ------------------------------------------------------------------ 基础
    @staticmethod
    def _norm(path: str) -> str:
        return path.replace("\\", "/").strip().lstrip("./")

    def reset(self) -> None:
        self._files = dict(self._originals)
        self._edits.clear()

    @property
    def files(self) -> Mapping[str, str]:
        return dict(self._files)

    @property
    def originals(self) -> Mapping[str, str]:
        return dict(self._originals)

    @property
    def edits(self) -> list[EditRecord]:
        return list(self._edits)

    @property
    def paths(self) -> list[str]:
        return list(self._files)

    def resolve(self, path: str | None) -> str | None:
        """把模型给的路径宽松归一化（允许 basename / 相对路径 / 绝对路径）。"""

        if not path:
            return next(iter(self._files), None)
        norm = self._norm(path)
        if norm in self._files:
            return norm
        basename = norm.rsplit("/", 1)[-1]
        candidates = [p for p in self._files if p.rsplit("/", 1)[-1] == basename]
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            suffix_hit = [p for p in candidates if norm.endswith(p)]
            if len(suffix_hit) == 1:
                return suffix_hit[0]
        return None

    def read(self, path: str) -> str:
        resolved = self.resolve(path)
        if resolved is None:
            raise KeyError(path)
        return self._files[resolved]

    def write(self, path: str, content: str) -> None:
        resolved = self.resolve(path)
        if resolved is None:
            raise KeyError(path)
        self._files[resolved] = content

    # ------------------------------------------------------------------ 编辑
    def apply_edit(self, path: str, old: str, new: str) -> EditOutcome:
        resolved = self.resolve(path)
        if resolved is None:
            return EditOutcome(
                ok=False,
                message=f"文件不存在: {path}。可用文件: {self.paths}",
            )
        content = self._files[resolved]

        if not old:
            return EditOutcome(ok=False, message="old 不能为空；请从当前文件复制要替换的原文。")
        if old == new:
            return EditOutcome(ok=False, message="old 与 new 相同，本次调用不会产生任何改动。")

        occurrences = content.count(old)
        if occurrences == 0:
            return EditOutcome(
                ok=False,
                message=self._mismatch_message(content, old),
                occurrences=0,
            )
        if occurrences > 1:
            line_numbers = self._occurrence_lines(content, old)
            return EditOutcome(
                ok=False,
                message=(
                    f"old 在 {resolved} 中匹配到 {occurrences} 处（行 {line_numbers}），"
                    "请扩大上下文使其唯一。"
                ),
                occurrences=occurrences,
            )

        before = content
        after = content.replace(old, new, 1)
        self._files[resolved] = after
        self._edits.append(EditRecord(path=resolved, old=old, new=new))
        return EditOutcome(
            ok=True,
            message=f"已应用改动到 {resolved}（+{len(new.splitlines())} / -{len(old.splitlines())} 行）。",
            diff=self._file_diff(FilePatch(resolved, before, after)),
            occurrences=1,
        )

    @staticmethod
    def _occurrence_lines(content: str, old: str) -> list[int]:
        lines: list[int] = []
        start = 0
        while True:
            idx = content.find(old, start)
            if idx < 0:
                break
            lines.append(content.count("\n", 0, idx) + 1)
            start = idx + 1
        return lines

    def _mismatch_message(self, content: str, old: str) -> str:
        """匹配失败时返回真实内容片段，而不是抛异常。"""

        hint = ""
        stripped = old.strip()
        if stripped and stripped in content:
            hint = "（old 去掉首尾空白后能匹配：缩进/空行必须与原文完全一致）"

        excerpt = self._nearest_excerpt(content, old)
        return (
            f"old 片段未在文件中找到{hint}。实际内容如下（行号不是内容的一部分）：\n{excerpt}"
        )

    @staticmethod
    def _nearest_excerpt(content: str, old: str, window: int = 4) -> str:
        lines = content.splitlines()
        anchor = next((line.strip() for line in old.splitlines() if line.strip()), "")
        hit = -1
        if anchor:
            for idx, line in enumerate(lines):
                if anchor in line:
                    hit = idx
                    break
        if hit < 0:
            matcher = difflib.SequenceMatcher(None, content, old, autojunk=False)
            block = matcher.find_longest_match(0, len(content), 0, len(old))
            if block.size >= 8:
                hit = content.count("\n", 0, block.a)
        if hit < 0:
            head = lines[:40]
            return "\n".join(f"{i + 1:>4} | {line}" for i, line in enumerate(head))
        low = max(0, hit - window)
        high = min(len(lines), hit + window + 1)
        return "\n".join(f"{i + 1:>4} | {lines[i]}" for i in range(low, high))

    # ------------------------------------------------------------------ 产物
    def patches(self) -> list[FilePatch]:
        return [
            FilePatch(path, self._originals[path], self._files[path])
            for path in self._files
            if self._files[path] != self._originals[path]
        ]

    def _file_diff(self, patch: FilePatch) -> str:
        diff = difflib.unified_diff(
            patch.before.splitlines(keepends=True),
            patch.after.splitlines(keepends=True),
            fromfile=f"a/{patch.path}",
            tofile=f"b/{patch.path}",
            n=3,
        )
        return "".join(diff)

    def unified_diff(self) -> str:
        return "".join(self._file_diff(p) for p in self.patches())

    def changed(self) -> bool:
        return bool(self.patches())

    def substantive_change(self) -> bool:
        """排除纯空白/空行改动，避免把格式噪声当成修复。"""

        def normalize(text: str) -> list[str]:
            return [line.strip() for line in text.splitlines() if line.strip()]

        return any(
            normalize(self._originals[p]) != normalize(self._files[p]) for p in self._files
        )

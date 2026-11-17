from __future__ import annotations

from typing import Callable, Sequence

from codingagent.domain.models import ChatMessage, LLMResponse

from .base import LLMError


class ScriptedLLMClient:
    """离线脚本化客户端：只用于跑通 harness 的自检，不代表任何真实指标。

    产物写进独立的 selftest 数据库，绝不进入正式实验库。
    """

    def __init__(
        self,
        responses: Sequence[str] | Callable[[Sequence[ChatMessage]], str],
        *,
        model: str = "scripted",
        labels: dict[str, str] | None = None,
    ) -> None:
        self._responses = list(responses) if not callable(responses) else None
        self._responder = responses if callable(responses) else None
        self.model = model
        self._labels = labels or {}
        self._index = 0
        self.calls: list[list[ChatMessage]] = []

    def complete(
        self, messages: Sequence[ChatMessage], *, json_mode: bool = False
    ) -> LLMResponse:
        self.calls.append(list(messages))
        if self._responder is not None:
            text = self._responder(messages)
        else:
            assert self._responses is not None
            if self._index >= len(self._responses):
                raise LLMError("脚本化客户端响应已耗尽")
            text = self._responses[self._index]
        self._index += 1
        prompt_chars = sum(len(m.content) for m in messages)
        return LLMResponse(
            text=text,
            model=self.model,
            prompt_tokens=prompt_chars // 4,
            completion_tokens=len(text) // 4,
            latency_ms=0,
            finish_reason="stop",
        )


def golden_bitcount_script() -> list[str]:
    """bitcount 的真实修复路径（n ^= n-1 → n &= n-1），仅用于 harness 自检。"""

    return [
        '{"action": "ast_slice", "args": {"path": "python_programs/bitcount.py", '
        '"symbol": "bitcount"}, "reason": "先定位目标函数"}',
        '{"action": "edit_file", "args": {"path": "python_programs/bitcount.py", '
        '"old": "        n ^= n - 1", "new": "        n &= n - 1"}, '
        '"reason": "修正位运算的运算符"}',
        '{"action": "run_test", "args": {}, "reason": "用官方测试验证"}',
        '{"action": "finish", "args": {}, "reason": "测试通过"}',
    ]

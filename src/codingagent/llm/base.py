"""LLM 客户端抽象。不要用框架把 Agent 内部黑盒掉。"""

from __future__ import annotations

from typing import Protocol, Sequence

from codingagent.domain.models import ChatMessage, LLMResponse


class LLMError(RuntimeError):
    """API 重试耗尽后抛出，由 Agent Loop 捕获并降级，不向上冒泡。"""


class LLMClient(Protocol):
    model: str

    def complete(
        self, messages: Sequence[ChatMessage], *, json_mode: bool = False
    ) -> LLMResponse: ...

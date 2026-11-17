from __future__ import annotations

import json

from codingagent.domain.models import Action


class LoopGuard:
    """同一个工具 + 同一参数连续出现 2 次即判定死循环。"""

    def __init__(self, repeat_threshold: int = 2) -> None:
        self.repeat_threshold = repeat_threshold
        self._last_key: str | None = None
        self._repeats = 0

    @staticmethod
    def _key(action: Action) -> str:
        return action.action.value + "|" + json.dumps(
            action.args, ensure_ascii=False, sort_keys=True, default=str
        )

    def observe(self, action: Action) -> bool:
        key = self._key(action)
        if key == self._last_key:
            self._repeats += 1
        else:
            self._last_key = key
            self._repeats = 1
        return self._repeats >= self.repeat_threshold

    def reset(self) -> None:
        self._last_key = None
        self._repeats = 0


class TokenBudget:
    """与步数上限并列的第二个独立停止条件。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.spent = 0

    def charge(self, tokens: int) -> None:
        self.spent += max(tokens, 0)

    @property
    def exceeded(self) -> bool:
        return self.spent >= self.limit

    @property
    def remaining(self) -> int:
        return max(self.limit - self.spent, 0)

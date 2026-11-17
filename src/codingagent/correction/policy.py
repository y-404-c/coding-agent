from __future__ import annotations

from codingagent.domain.enums import ErrorCategory, FeedbackStyle
from codingagent.domain.models import Classification


class CorrectionPolicy:
    """重试上限 2 次；不可纠类别直接降级，绝不无限重试。"""

    def __init__(self, max_retries: int = 2, style: FeedbackStyle = FeedbackStyle.RAW) -> None:
        self.max_retries = max_retries
        self.style = style

    def should_retry(self, classification: Classification, attempt_index: int) -> bool:
        if self.style == FeedbackStyle.NONE:
            return False
        if attempt_index >= self.max_retries:
            return False
        if classification.category in (ErrorCategory.NONE,):
            return False
        return classification.retryable

    def degrade_reason(self, classification: Classification, attempt_index: int) -> str:
        if classification.category == ErrorCategory.NONE:
            return ""
        if not classification.retryable:
            return (
                f"错误类别 {classification.category.value} 属于不可纠类型"
                "（超时/OOM/环境/不明），已直接降级，不再重试。"
            )
        if attempt_index >= self.max_retries:
            return f"已达到重试上限 {self.max_retries} 次，停止重试并输出兜底结果。"
        return ""

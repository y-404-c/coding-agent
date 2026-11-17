from __future__ import annotations

from enum import Enum


class Strategy(str, Enum):
    """上下文策略。除上下文构造方式外，其余变量全部固定。"""

    S0 = "S0"  # 全文件基线
    S1 = "S1"  # AST 切片
    S2 = "S2"  # S1 + 测试上下文
    S3 = "S3"  # S2 + few-shot
    S4 = "S4"  # S3 + 结构化报错回喂（自纠正）

    def __str__(self) -> str:  # pragma: no cover - 仅用于展示
        return self.value

    @property
    def label(self) -> str:
        return STRATEGY_LABELS[self]


STRATEGY_LABELS: dict[Strategy, str] = {
    Strategy.S0: "S0 全文件基线",
    Strategy.S1: "S1 AST 切片",
    Strategy.S2: "S2 AST 切片 + 测试上下文",
    Strategy.S3: "S3 + few-shot 范例",
    Strategy.S4: "S4 + 结构化报错回喂",
}

STRATEGY_HYPOTHESES: dict[Strategy, str] = {
    Strategy.S0: "朴素做法的上限在哪",
    Strategy.S1: "减少噪声是否提升准确率、节省 token",
    Strategy.S2: "给出正确性信号是否优于只看实现",
    Strategy.S3: "相似修复示例能否进一步提升",
    Strategy.S4: "失败后的结构化回喂能救回多少题",
}


class ActionType(str, Enum):
    READ_FILE = "read_file"
    GREP = "grep"
    AST_SLICE = "ast_slice"
    EDIT_FILE = "edit_file"
    RUN_TEST = "run_test"
    FINISH = "finish"


class ErrorCategory(str, Enum):
    """失败归因类别。可纠/不可纠的边界在这里硬编码，不靠模型判断。"""

    NONE = "none"
    SYNTAX_ERROR = "syntax_error"
    NAME_ERROR = "name_error"
    IMPORT_ERROR = "import_error"
    RUNTIME_ERROR = "runtime_error"
    ASSERTION_FAILURE = "assertion_failure"
    TIMEOUT = "timeout"
    MEMORY_ERROR = "memory_error"
    ENV_ERROR = "env_error"
    EMPTY_PATCH = "empty_patch"
    NOOP_PATCH = "noop_patch"
    UNKNOWN = "unknown"

    @property
    def retryable(self) -> bool:
        return self in RETRYABLE_CATEGORIES


# 可纠：代码自身的问题，回喂后重试有意义
# 不可纠：超时/OOM/依赖环境/不明类别，重试只会再烧钱
RETRYABLE_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.SYNTAX_ERROR,
        ErrorCategory.NAME_ERROR,
        ErrorCategory.IMPORT_ERROR,
        ErrorCategory.RUNTIME_ERROR,
        ErrorCategory.ASSERTION_FAILURE,
        ErrorCategory.EMPTY_PATCH,
        ErrorCategory.NOOP_PATCH,
    }
)

NON_RETRYABLE_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.TIMEOUT,
        ErrorCategory.MEMORY_ERROR,
        ErrorCategory.ENV_ERROR,
        ErrorCategory.UNKNOWN,
    }
)


class FeedbackStyle(str, Enum):
    """回喂形态：raw=裸报错文本；structured=第 6.2 节的结构化 JSON。"""

    NONE = "none"
    RAW = "raw"
    STRUCTURED = "structured"

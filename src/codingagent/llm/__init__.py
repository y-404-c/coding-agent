from __future__ import annotations

from codingagent.config import LLMSettings

from .base import LLMClient, LLMError
from .openai_compatible import OpenAICompatibleClient
from .scripted import ScriptedLLMClient, golden_bitcount_script

__all__ = [
    "LLMClient",
    "LLMError",
    "OpenAICompatibleClient",
    "ScriptedLLMClient",
    "build_llm",
    "golden_bitcount_script",
]


def build_llm(settings: LLMSettings, *, scripted_responses: list[str] | None = None) -> LLMClient:
    if settings.provider == "scripted":
        return ScriptedLLMClient(scripted_responses or golden_bitcount_script())
    if not settings.resolved_api_key():
        raise LLMError(
            "未配置 API key：请在 .env 里设置 CA_LLM__API_KEY（或 DEEPSEEK_API_KEY）。"
        )
    return OpenAICompatibleClient(settings)

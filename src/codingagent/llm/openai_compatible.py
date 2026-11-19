from __future__ import annotations

import time
from typing import Sequence

import httpx

from codingagent.config import LLMSettings
from codingagent.domain.models import ChatMessage, LLMResponse

from .base import LLMError


class OpenAICompatibleClient:
    """最小实现的 OpenAI 兼容客户端（DeepSeek / 通义 / 智谱 / vLLM 均可）。

    - 指数退避重试 429/5xx/网络错误；
    - 服务端不支持 response_format 时自动降级并记住该能力；
    - 不依赖 openai SDK，调用次数与用量完全可控。
    """

    def __init__(self, settings: LLMSettings, *, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self.model = settings.model
        self._json_mode_supported = settings.json_mode
        self._client = client or httpx.Client(timeout=settings.timeout_s)

    def complete(
        self, messages: Sequence[ChatMessage], *, json_mode: bool = False
    ) -> LLMResponse:
        payload: dict[str, object] = {
            "model": self._settings.model,
            "messages": [m.model_dump() for m in messages],
            "temperature": self._settings.temperature,
            "max_tokens": self._settings.max_tokens,
        }
        use_json = bool(json_mode and self._json_mode_supported)
        if use_json:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        started = time.perf_counter()
        for attempt in range(self._settings.max_retries + 1):
            try:
                response = self._client.post(
                    f"{self._settings.base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._settings.resolved_api_key()}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if response.status_code == 400 and use_json and "response_format" in response.text:
                    # 少数代理/自建服务不支持 JSON 模式：降级后立刻重试一次
                    self._json_mode_supported = False
                    use_json = False
                    payload.pop("response_format", None)
                    continue
                if response.status_code in {429, 500, 502, 503, 504}:
                    last_error = LLMError(f"HTTP {response.status_code}: {response.text[:200]}")
                    self._sleep(attempt)
                    continue
                response.raise_for_status()
                body = response.json()
                choice = (body.get("choices") or [{}])[0]
                content = (choice.get("message") or {}).get("content") or ""
                usage = body.get("usage") or {}
                return LLMResponse(
                    text=content,
                    model=body.get("model", self._settings.model),
                    prompt_tokens=int(usage.get("prompt_tokens", 0)),
                    completion_tokens=int(usage.get("completion_tokens", 0)),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    finish_reason=choice.get("finish_reason", ""),
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                self._sleep(attempt)
            except httpx.HTTPStatusError as exc:
                # 网关偶发的 4xx/5xx（如 408、409、425、非标准码）可能被 raise_for_status
                # 抛到这里；这类是暂时性的，重试可救回一个本会降级的数据点。
                # 确定性错误（鉴权/路径/参数错）重试无用，直接放弃。
                if exc.response.status_code in {400, 401, 403, 404, 422}:
                    raise
                last_error = exc
                self._sleep(attempt)
        raise LLMError(f"LLM 调用失败（已重试 {self._settings.max_retries} 次）: {last_error}")

    @staticmethod
    def _sleep(attempt: int) -> None:
        time.sleep(min(2**attempt * 0.5, 8.0))

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "default.yaml"


class LLMSettings(BaseModel):
    provider: Literal["openai_compatible", "scripted"] = "openai_compatible"
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout_s: float = 60.0
    max_retries: int = 3
    json_mode: bool = True

    def resolved_api_key(self) -> str:
        """优先配置值，其次常见环境变量，方便不落盘密钥。"""

        if self.api_key:
            return self.api_key
        for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "CA_API_KEY"):
            value = os.environ.get(name, "")
            if value:
                return value
        return ""


class AgentSettings(BaseModel):
    max_steps: int = 10
    token_budget: int = 15_000
    max_format_retries: int = 2
    observation_max_chars: int = 4000
    context_max_chars: int = 12_000

    @field_validator("max_steps")
    @classmethod
    def _check_steps(cls, value: int) -> int:
        if not 1 <= value <= 30:
            raise ValueError("max_steps 应在 1..30 之间，推荐 8-12")
        return value


class CorrectionSettings(BaseModel):
    max_retries: int = 2
    feedback_style: dict[str, str] = Field(
        default_factory=lambda: {
            "S0": "raw",
            "S1": "raw",
            "S2": "raw",
            "S3": "raw",
            "S4": "structured",
        }
    )

    def style_for(self, strategy: str) -> str:
        return self.feedback_style.get(str(strategy), "raw")

    @field_validator("feedback_style")
    @classmethod
    def _check_style(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"none", "raw", "structured"}
        bad = {k: v for k, v in value.items() if v not in allowed}
        if bad:
            raise ValueError(f"feedback_style 只支持 {sorted(allowed)}，收到 {bad}")
        return value


class SandboxSettings(BaseModel):
    timeout_s: float = 10.0
    max_output_chars: int = 8000
    python_executable: str = sys.executable


class DatasetSettings(BaseModel):
    root: Path = PROJECT_ROOT / "data" / "quixbugs"
    source_url: str = (
        "https://codeload.github.com/jkoppel/QuixBugs/zip/refs/heads/master"
    )
    auto_fetch: bool = True


class EvalSettings(BaseModel):
    strategies: list[str] = Field(default_factory=lambda: ["S0", "S1", "S2", "S3", "S4"])
    repeats: int = 3
    db_path: Path = PROJECT_ROOT / "var" / "runs.db"
    reports_dir: Path = PROJECT_ROOT / "reports"


class Settings(BaseSettings):
    """YAML 为底、环境变量覆盖（CA_LLM__MODEL 这类）。"""

    model_config = SettingsConfigDict(
        env_prefix="CA_",
        env_nested_delimiter="__",
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    correction: CorrectionSettings = Field(default_factory=CorrectionSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    dataset: DatasetSettings = Field(default_factory=DatasetSettings)
    eval: EvalSettings = Field(default_factory=EvalSettings)

    @classmethod
    def load(cls, path: Path | str | None = None) -> Settings:
        config_path = Path(path) if path else DEFAULT_CONFIG_PATH
        data: dict[str, Any] = {}
        if config_path.exists():
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        llm_data = data.get("llm")
        if isinstance(llm_data, dict) and not llm_data.get("api_key"):
            # 空密钥只是 YAML 占位符，不能让 init source 覆盖环境变量或 .env。
            llm_data.pop("api_key", None)
        settings = cls(**data)
        settings.resolve_paths()
        return settings

    def resolve_paths(self) -> None:
        for holder, field in (
            (self.dataset, "root"),
            (self.eval, "db_path"),
            (self.eval, "reports_dir"),
        ):
            value = getattr(holder, field)
            if not Path(value).is_absolute():
                setattr(holder, field, (PROJECT_ROOT / value).resolve())

    def as_hashable(self) -> dict[str, Any]:
        """参与实验一致性校验的字段（不含密钥）。"""

        return {
            "llm": {
                "model": self.llm.model,
                "base_url": self.llm.base_url,
                "temperature": self.llm.temperature,
                "max_tokens": self.llm.max_tokens,
                "json_mode": self.llm.json_mode,
            },
            "agent": self.agent.model_dump(),
            "correction": self.correction.model_dump(),
            "sandbox": {"timeout_s": self.sandbox.timeout_s},
            "eval": {"strategies": self.eval.strategies},
        }

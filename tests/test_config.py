from __future__ import annotations

from codingagent.config import Settings


def test_ca_llm_api_key_env_overrides_blank_yaml(monkeypatch, tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text('llm:\n  api_key: ""\n', encoding="utf-8")
    monkeypatch.setenv("CA_LLM__API_KEY", "temporary-env-key")

    settings = Settings.load(config)

    assert settings.llm.api_key == "temporary-env-key"
    assert settings.llm.resolved_api_key() == "temporary-env-key"


def test_ca_llm_api_key_dotenv_overrides_blank_yaml(monkeypatch, tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text('llm:\n  api_key: ""\n', encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("CA_LLM__API_KEY=temporary-dotenv-key\n", encoding="utf-8")
    monkeypatch.delenv("CA_LLM__API_KEY", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", str(env_file))

    settings = Settings.load(config)

    assert settings.llm.api_key == "temporary-dotenv-key"
    assert settings.llm.resolved_api_key() == "temporary-dotenv-key"

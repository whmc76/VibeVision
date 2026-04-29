from app.core.config import Settings


def test_settings_resolve_logic_and_prompt_models_with_legacy_fallback() -> None:
    settings = Settings(
        ollama_model="base-model",
        ollama_logic_model="logic-model",
        ollama_prompt_model="",
    )

    assert settings.ollama_logic_model_name == "logic-model"
    assert settings.ollama_prompt_model_name == "base-model"
    assert settings.ollama_model_summary == "logic=logic-model, prompt=base-model"


def test_settings_reuse_single_specialized_model_for_both_roles() -> None:
    settings = Settings(
        ollama_model="",
        ollama_logic_model="",
        ollama_prompt_model="prompt-only-model",
    )

    assert settings.ollama_logic_model_name == "prompt-only-model"
    assert settings.ollama_prompt_model_name == "prompt-only-model"
    assert settings.ollama_model_summary == "prompt-only-model (logic + prompt)"


def test_settings_resolve_minimax_models_and_provider_alias() -> None:
    settings = Settings(
        llm_provider="m2.7",
        llm_logic_provider="m2.7",
        llm_prompt_provider="ollama",
        minimax_model="codex-MiniMax-M2.7",
        minimax_logic_model="",
        minimax_prompt_model="prompt-model",
        ollama_prompt_model="qwen-prompt-model",
    )

    assert settings.llm_provider_name == "minimax"
    assert settings.llm_logic_provider_name == "minimax"
    assert settings.llm_prompt_provider_name == "ollama"
    assert settings.minimax_logic_model_name == "codex-MiniMax-M2.7"
    assert settings.minimax_prompt_model_name == "prompt-model"
    assert settings.llm_model_summary == (
        "logic=MiniMax:codex-MiniMax-M2.7, prompt=Ollama:qwen-prompt-model"
    )


def test_settings_default_concurrency_limits_are_serial_for_ollama_and_comfyui() -> None:
    settings = Settings()

    assert settings.ollama_max_concurrency == 1
    assert settings.comfyui_max_concurrency == 1
    assert settings.gpu_idle_release_seconds == 5
    assert settings.telegram_poller_max_workers == 4
    assert settings.telegram_update_queue_url == ""
    assert settings.telegram_update_queue_workers == 1
    assert settings.telegram_update_queue_maxlen == 100_000
    assert settings.telegram_duplicate_message_window_seconds == 45


def test_settings_supports_minimax_mcp_vision_provider() -> None:
    settings = Settings(llm_vision_provider="coding-plan")

    assert settings.llm_vision_provider_name == "minimax_mcp"

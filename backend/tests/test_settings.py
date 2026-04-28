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

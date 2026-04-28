from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]
CONFIG_FILE = ROOT_DIR / "config" / "vibevision.env"
LOCAL_CONFIG_FILE = ROOT_DIR / "config" / "vibevision.local.env"


class Settings(BaseSettings):
    app_name: str = "VibeVision"
    environment: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 18751
    admin_frontend_host: str = "127.0.0.1"
    admin_frontend_port: int = 18742
    admin_cors_origins: str = "http://localhost:18742,http://127.0.0.1:18742"
    database_url: str = "sqlite:///./data/vibevision.db"
    ollama_host: str = "127.0.0.1"
    ollama_port: int = 11434
    ollama_model: str = "huihui_ai/qwen3.5-abliterated:9b"
    ollama_logic_model: str = ""
    ollama_prompt_model: str = ""
    ollama_vision_max_bytes: int = 8_000_000
    comfyui_host: str = "127.0.0.1"
    comfyui_port: int = 8401
    comfyui_root: str = ""
    comfyui_start_script: str = "run_nvidia_gpu.bat"
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = Field(default="", repr=False)
    comfyui_poll_interval_seconds: int = 3
    comfyui_poll_timeout_seconds: int = 600

    model_config = SettingsConfigDict(
        env_file=(CONFIG_FILE, LOCAL_CONFIG_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("database_url")
    @classmethod
    def normalize_sqlite_path(cls, value: str) -> str:
        prefix = "sqlite:///./"
        if value.startswith(prefix):
            database_path = ROOT_DIR / value.removeprefix(prefix)
            database_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{database_path.as_posix()}"
        return value

    @field_validator("ollama_model", "ollama_logic_model", "ollama_prompt_model", mode="before")
    @classmethod
    def normalize_model_name(cls, value: str | None) -> str:
        return str(value or "").strip()

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.admin_cors_origins.split(",") if origin.strip()]

    @property
    def api_base_url(self) -> str:
        return f"http://{self.api_host}:{self.api_port}"

    @property
    def ollama_base_url(self) -> str:
        return f"http://{self.ollama_host}:{self.ollama_port}"

    @property
    def comfyui_base_url(self) -> str:
        return f"http://{self.comfyui_host}:{self.comfyui_port}"

    @property
    def ollama_logic_model_name(self) -> str:
        return self.ollama_logic_model or self.ollama_model or self.ollama_prompt_model

    @property
    def ollama_prompt_model_name(self) -> str:
        return self.ollama_prompt_model or self.ollama_model or self.ollama_logic_model

    @property
    def ollama_model_summary(self) -> str:
        logic_model = self.ollama_logic_model_name
        prompt_model = self.ollama_prompt_model_name
        if logic_model and logic_model == prompt_model:
            return f"{logic_model} (logic + prompt)"
        parts: list[str] = []
        if logic_model:
            parts.append(f"logic={logic_model}")
        if prompt_model:
            parts.append(f"prompt={prompt_model}")
        return ", ".join(parts) or "Models are not configured"


@lru_cache
def get_settings() -> Settings:
    return Settings()

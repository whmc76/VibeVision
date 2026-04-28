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
    api_port: int = 18741
    admin_frontend_host: str = "127.0.0.1"
    admin_frontend_port: int = 18742
    admin_cors_origins: str = "http://localhost:18742,http://127.0.0.1:18742"
    database_url: str = "sqlite:///./data/vibevision.db"
    ollama_host: str = "127.0.0.1"
    ollama_port: int = 18743
    ollama_model: str = "qwen2.5:7b"
    comfyui_host: str = "127.0.0.1"
    comfyui_port: int = 18744
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = Field(default="", repr=False)

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


@lru_cache
def get_settings() -> Settings:
    return Settings()

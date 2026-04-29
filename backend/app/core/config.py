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
    llm_provider: str = "ollama"
    llm_logic_provider: str = ""
    llm_prompt_provider: str = ""
    llm_vision_provider: str = ""
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
    ollama_max_concurrency: int = 1
    gpu_idle_release_seconds: int = 5
    minimax_base_url: str = "https://api.minimaxi.com/v1"
    minimax_api_host: str = "https://api.minimaxi.com"
    minimax_api_key: str = Field(default="", repr=False)
    minimax_model: str = "codex-MiniMax-M2.7"
    minimax_logic_model: str = ""
    minimax_prompt_model: str = ""
    minimax_timeout_seconds: int = 60
    minimax_vision_timeout_seconds: int = 60
    minimax_vision_max_bytes: int = 8_000_000
    comfyui_host: str = "127.0.0.1"
    comfyui_port: int = 8401
    comfyui_root: str = ""
    comfyui_start_script: str = "run_nvidia_gpu.bat"
    comfyui_max_concurrency: int = 1
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = Field(default="", repr=False)
    telegram_poller_max_workers: int = 4
    telegram_update_queue_url: str = Field(default="", repr=False)
    telegram_update_queue_stream: str = "vibevision:telegram:updates"
    telegram_update_queue_group: str = "vibevision"
    telegram_update_queue_consumer_prefix: str = "local"
    telegram_update_queue_workers: int = 1
    telegram_update_queue_maxlen: int = 100_000
    telegram_update_queue_block_ms: int = 5_000
    telegram_duplicate_message_window_seconds: int = 45
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

    @field_validator(
        "llm_provider",
        "llm_logic_provider",
        "llm_prompt_provider",
        "llm_vision_provider",
        mode="before",
    )
    @classmethod
    def normalize_llm_provider(cls, value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return ""
        if normalized in {"minimax_mcp", "minimax-mcp", "coding-plan", "coding_plan"}:
            return "minimax_mcp"
        if normalized in {"minimax", "mini-max", "m2", "m2.7"}:
            return "minimax"
        if normalized in {"none", "off", "disabled"}:
            return "none"
        return "ollama"

    @field_validator(
        "ollama_model",
        "ollama_logic_model",
        "ollama_prompt_model",
        "minimax_model",
        "minimax_logic_model",
        "minimax_prompt_model",
        mode="before",
    )
    @classmethod
    def normalize_model_name(cls, value: str | None) -> str:
        return str(value or "").strip()

    @field_validator("minimax_base_url", mode="before")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str:
        return str(value or "https://api.minimaxi.com/v1").strip().rstrip("/")

    @field_validator("minimax_api_host", mode="before")
    @classmethod
    def normalize_api_host(cls, value: str | None) -> str:
        normalized = str(value or "https://api.minimaxi.com").strip().rstrip("/")
        return normalized.removesuffix("/v1")

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
    def llm_provider_name(self) -> str:
        return self.llm_provider or "ollama"

    @property
    def llm_logic_provider_name(self) -> str:
        return self.llm_logic_provider or self.llm_provider_name

    @property
    def llm_prompt_provider_name(self) -> str:
        return self.llm_prompt_provider or self.llm_provider_name

    @property
    def llm_vision_provider_name(self) -> str:
        return self.llm_vision_provider or "minimax_mcp"

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
    def minimax_logic_model_name(self) -> str:
        return self.minimax_logic_model or self.minimax_model or self.minimax_prompt_model

    @property
    def minimax_prompt_model_name(self) -> str:
        return self.minimax_prompt_model or self.minimax_model or self.minimax_logic_model

    @property
    def ollama_model_summary(self) -> str:
        logic_model = self.ollama_logic_model_name
        prompt_model = self.ollama_prompt_model_name
        return self._model_summary(logic_model, prompt_model)

    @property
    def minimax_model_summary(self) -> str:
        logic_model = self.minimax_logic_model_name
        prompt_model = self.minimax_prompt_model_name
        return self._model_summary(logic_model, prompt_model)

    @property
    def llm_model_summary(self) -> str:
        logic = self._role_model_summary(
            role="logic",
            provider=self.llm_logic_provider_name,
            ollama_model=self.ollama_logic_model_name,
            minimax_model=self.minimax_logic_model_name,
        )
        prompt = self._role_model_summary(
            role="prompt",
            provider=self.llm_prompt_provider_name,
            ollama_model=self.ollama_prompt_model_name,
            minimax_model=self.minimax_prompt_model_name,
        )
        return f"{logic}, {prompt}"

    def _model_summary(self, logic_model: str, prompt_model: str) -> str:
        if logic_model and logic_model == prompt_model:
            return f"{logic_model} (logic + prompt)"
        parts: list[str] = []
        if logic_model:
            parts.append(f"logic={logic_model}")
        if prompt_model:
            parts.append(f"prompt={prompt_model}")
        return ", ".join(parts) or "Models are not configured"

    def _role_model_summary(
        self,
        *,
        role: str,
        provider: str,
        ollama_model: str,
        minimax_model: str,
    ) -> str:
        if provider in {"minimax", "minimax_mcp"}:
            return f"{role}=MiniMax:{minimax_model or 'not configured'}"
        return f"{role}=Ollama:{ollama_model or 'not configured'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()

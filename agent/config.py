from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./healthagent.db"
    model_provider: str = "fake"
    model_base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4o-mini"
    understanding_light_model: str = "qwen3:4b"
    understanding_strong_model: str = "qwen3:14b"
    run_s25_01_live: bool = False
    route_shadow_enabled: bool = False  # M2 opt-in, never affects admission
    understanding_strategy: str = "direct"  # direct or structured; benchmark before default switch
    model_api_key: str = ""
    model_timeout_seconds: float = Field(default=45, gt=0)
    model_max_output_tokens: int = Field(default=2048, ge=128, le=16384)
    max_history_messages: int = Field(default=30, ge=1, le=200)
    context_max_chars: int = Field(default=24000, ge=128, le=200000)
    max_input_chars: int = Field(default=12000, ge=1)
    skill_strategy: str = "dynamic"
    skill_budget_chars: int = Field(default=600, ge=0, le=20000)
    demo_user_id: str = "demo-local"
    # Not an authentication system: development-only bearer tokens map to identities.
    user_tokens: str = ""
    allow_demo_identity: bool = True
    @property
    def provider_ready(self) -> bool:
        return self.model_provider in ("fake", "ollama_proxy") or bool(self.model_api_key)

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator
from typing import Literal

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./healthagent.db"
    model_provider: str = "fake"
    model_base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4o-mini"
    understanding_light_model: str = "qwen3:4b"
    understanding_strong_model: str = "qwen3:14b"
    run_s25_01_live: bool = False
    routing_profile: Literal["auto", "unified", "legacy"] = "auto"
    route_shadow_enabled: bool = False  # M2 opt-in, never affects admission
    route_admission_mode: Literal["legacy", "guarded"] = "legacy"  # M4 explicit opt-in
    m5_first_route_enabled: bool = False  # explicit FIRST-only experiment
    m5_unified_route_enabled: bool = False  # opt-in shared saved-plan route experiment
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
    @model_validator(mode="after")
    def resolve_routing_profile(self):
        # Auto selects the new default only for a real provider without an
        # explicit legacy/experimental routing override. Fake model fixtures
        # and existing explicit settings remain backward-compatible.
        fields = {"route_admission_mode", "understanding_strategy",
                  "m5_unified_route_enabled", "m5_first_route_enabled"}
        explicit_flags = fields & self.model_fields_set
        target = self.routing_profile
        if target == "auto":
            if not explicit_flags and self.model_provider != "fake":
                target = "unified"
            else:
                # Explicit legacy/M4 compatibility is supported, but a
                # half-enabled M5 or FIRST-only gate must never silently
                # degrade to legacy/direct or bypass guarded admission.
                if self.m5_unified_route_enabled and (
                        self.route_admission_mode != "guarded"
                        or self.understanding_strategy != "structured"
                        or self.m5_first_route_enabled):
                    raise ValueError("inconsistent_unified_route_flags")
                if self.m5_first_route_enabled and (
                        self.route_admission_mode != "guarded"
                        or self.understanding_strategy != "structured"):
                    raise ValueError("inconsistent_first_route_flags")
                return self
        values = {
            "unified": ("guarded", "structured", True, False),
            "legacy": ("legacy", "direct", False, False),
        }
        wanted = dict(zip(("route_admission_mode", "understanding_strategy",
                           "m5_unified_route_enabled", "m5_first_route_enabled"),
                          values[target]))
        for key in explicit_flags:
            if getattr(self, key) != wanted[key]:
                raise ValueError("routing_profile_conflicts_with_" + key)
        for key, value in wanted.items():
            setattr(self, key, value)
        return self

    @property
    def effective_routing_profile(self) -> str:
        if (self.route_admission_mode == "guarded"
                and self.understanding_strategy == "structured"
                and self.m5_unified_route_enabled
                and not self.m5_first_route_enabled):
            return "unified"
        if (self.route_admission_mode == "legacy"
                and self.understanding_strategy == "direct"
                and not self.m5_unified_route_enabled
                and not self.m5_first_route_enabled):
            return "legacy"
        return "compatibility"

    @property
    def provider_ready(self) -> bool:
        return self.model_provider in ("fake", "ollama_proxy") or bool(self.model_api_key)

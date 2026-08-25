"""Configuration management."""

from pathlib import Path
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
import yaml


class RateLimitConfig(BaseModel):
    """Rate limiting configuration."""
    max_actions_per_minute: int = 5
    max_actions_per_hour: int = 20


class LLMConfig(BaseModel):
    """LLM configuration."""
    model: str = "claude-sonnet-5-20241022"
    max_tokens: int = 4096
    temperature: float = 0.0
    timeout_seconds: int = 30


class PolicyConfig(BaseModel):
    """Policy configuration."""
    auto_approve_low_risk: bool = False
    dry_run_only: bool = True  # Start safe!
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)


class WatcherConfig(BaseModel):
    """Watcher configuration."""
    namespaces: list[str] = Field(default_factory=lambda: ["default"])
    watch_interval_seconds: int = 30
    min_restart_count_threshold: int = 3  # Ignore pods with <3 restarts


class Settings(BaseSettings):
    """Application settings."""
    anthropic_api_key: str = Field(alias="ANTHROPIC_API_KEY")
    kubeconfig_path: str | None = None  # None = in-cluster

    llm: LLMConfig = Field(default_factory=LLMConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    watcher: WatcherConfig = Field(default_factory=WatcherConfig)

    log_level: str = "INFO"
    audit_log_path: Path = Path("logs/audit.jsonl")

    class Config:
        env_file = ".env"
        env_nested_delimiter = "__"


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from environment and optional YAML config."""
    settings = Settings()

    if config_path and config_path.exists():
        with open(config_path) as f:
            config_data = yaml.safe_load(f)
            # Merge YAML config over defaults
            for key, value in config_data.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)

    # Ensure audit log directory exists
    settings.audit_log_path.parent.mkdir(parents=True, exist_ok=True)

    return settings

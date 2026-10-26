"""Application settings loaded from environment variables.

Validated eagerly at import/startup time (see main.py) so the process fails
fast with an actionable message instead of crashing on the first request.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEKEEPER_", env_file=".env", extra="ignore")

    env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")  # "json" or "console"

    admin_username: str = Field(default="admin")
    admin_password: str = Field(default="change-me-admin-password")

    database_url: str = Field(default="postgresql+asyncpg://gatekeeper:gatekeeper@localhost:5432/gatekeeper")
    db_pool_size: int = Field(default=10)
    db_max_overflow: int = Field(default=5)
    db_pool_timeout_seconds: float = Field(default=5.0)

    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_connect_timeout_seconds: float = Field(default=1.0)
    redis_command_timeout_seconds: float = Field(default=0.5)

    rate_limit_fail_mode: str = Field(default="fail_closed")  # fail_closed | fail_open

    routes_config: str = Field(default="config/routes.yaml")
    policies_config: str = Field(default="config/policies.yaml")

    max_request_body_bytes: int = Field(default=2 * 1024 * 1024)  # 2 MiB
    max_response_body_bytes: int = Field(default=10 * 1024 * 1024)  # 10 MiB

    api_key_prefix: str = Field(default="gk")

    @field_validator("rate_limit_fail_mode")
    @classmethod
    def _validate_fail_mode(cls, v: str) -> str:
        if v not in {"fail_open", "fail_closed"}:
            raise ValueError(
                f"GATEKEEPER_RATE_LIMIT_FAIL_MODE must be 'fail_open' or 'fail_closed', got {v!r}"
            )
        return v

    @field_validator("log_format")
    @classmethod
    def _validate_log_format(cls, v: str) -> str:
        if v not in {"json", "console"}:
            raise ValueError(f"GATEKEEPER_LOG_FORMAT must be 'json' or 'console', got {v!r}")
        return v


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide Settings singleton, constructing it on first use."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """Clear the cached Settings singleton. Used by tests that mutate env vars."""
    global _settings
    _settings = None

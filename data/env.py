"""Environment-variable settings and secret validation."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvironmentSettings(BaseSettings):
    """Runtime settings. Secrets are represented by redacting ``SecretStr`` values."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    eodhd_api_key: SecretStr | None = None
    database_url: SecretStr | None = None
    resend_api_key: SecretStr | None = None
    email_from: str | None = None
    email_to: str | None = None
    app_url: str = "http://localhost:8501"
    timezone: str = "Asia/Tokyo"
    eodhd_base_url: str = "https://eodhd.com/api"
    http_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    http_max_retries: int = Field(default=3, ge=0, le=10)
    log_level: str = "INFO"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("TIMEZONE must be an IANA timezone") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def valid_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL is invalid")
        return normalized

    def require_eodhd_key(self) -> str:
        if self.eodhd_api_key is None or not self.eodhd_api_key.get_secret_value():
            raise ValueError("EODHD_API_KEY is required for EODHD requests")
        return self.eodhd_api_key.get_secret_value()

    def require_database_url(self) -> str:
        if self.database_url is None or not self.database_url.get_secret_value():
            raise ValueError("DATABASE_URL is required for database operations")
        return self.database_url.get_secret_value()

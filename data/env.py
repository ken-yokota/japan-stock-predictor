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
    # The hosted database. On the operator's machine DATABASE_URL points at a
    # local copy that has the schema and none of the rows, so a report that
    # reads DATABASE_URL there shows an empty production and a size in MB, both
    # of which look like answers. In GitHub Actions only DATABASE_URL is set
    # and it is the hosted one, so preferring this when present is correct in
    # both places.
    neon_database_url: SecretStr | None = None
    resend_api_key: SecretStr | None = None
    email_provider: str = "gmail_smtp"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
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

    @field_validator("email_provider")
    @classmethod
    def valid_email_provider(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"gmail_smtp", "resend", "dry_run"}:
            raise ValueError("EMAIL_PROVIDER must be gmail_smtp, resend, or dry_run")
        return normalized

    def require_eodhd_key(self) -> str:
        if self.eodhd_api_key is None or not self.eodhd_api_key.get_secret_value():
            raise ValueError("EODHD_API_KEY is required for EODHD requests")
        return self.eodhd_api_key.get_secret_value()

    def require_database_url(self) -> str:
        if self.database_url is None or not self.database_url.get_secret_value():
            raise ValueError("DATABASE_URL is required for database operations")
        return self.database_url.get_secret_value()

    def reporting_database_url(self) -> str:
        """The database to *read* when reporting on production.

        Deliberately separate from ``require_database_url``: that one feeds the
        write path, and silently redirecting writes to the hosted database
        because a second variable happened to be set is not a change a report
        should make.
        """

        # An explicitly exported DATABASE_URL wins. A caller that sets it --
        # a test harness pointing at a scratch database, a workflow naming its
        # own environment -- has stated where to look, and silently redirecting
        # that to the hosted database because a second variable exists in .env
        # would send test writes to production.
        import os

        explicit = os.environ.get("DATABASE_URL", "").strip()
        if explicit:
            return explicit
        if self.neon_database_url and self.neon_database_url.get_secret_value():
            return self.neon_database_url.get_secret_value()
        return self.require_database_url()

    def require_email_addresses(self) -> tuple[str, str]:
        """Return sender/recipient addresses for a real or dry-run message."""

        if not self.email_from or not self.email_to:
            raise ValueError("EMAIL_FROM and EMAIL_TO are required for email delivery")
        return self.email_from, self.email_to

    def require_gmail_credentials(self) -> tuple[str, str]:
        """Fail fast when Gmail SMTP is selected without an App Password."""

        password = (
            self.smtp_password.get_secret_value()
            if self.smtp_password is not None
            else ""
        )
        if not self.smtp_username or not password:
            raise ValueError(
                "SMTP_USERNAME and SMTP_PASSWORD are required for Gmail SMTP"
            )
        return self.smtp_username, password

    def require_resend_key(self) -> str:
        """Return the optional Resend key only when that provider is selected."""

        secret = (
            self.resend_api_key.get_secret_value()
            if self.resend_api_key is not None
            else ""
        )
        if not secret:
            raise ValueError("RESEND_API_KEY is required when EMAIL_PROVIDER=resend")
        return secret

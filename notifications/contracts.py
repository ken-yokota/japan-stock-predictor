"""Notification-domain contracts without database or provider dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmailCandidate:
    """One ranked prediction rendered in the morning message."""

    ticker: str
    company: str
    predicted_return: float | None
    probability_up: float | None
    signal: str
    status: str = "READY"
    # The two prices the operator checks first: what it closed at yesterday and
    # where the model expects it to close today.
    reference_price: float | None = None
    predicted_close: float | None = None
    rank: int | None = None
    readability_score: float | None = None
    profit_factor: float | None = None
    expectancy_jpy: float | None = None
    positive_factors: tuple[str, ...] = field(default_factory=tuple)
    negative_factors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    # CLEAN / DEGRADED / LEGACY_UNKNOWN, from dashboard.completeness. Defaults
    # to the honest answer: a message built before completeness was recorded
    # cannot claim the inputs were whole.
    data_quality: str = "LEGACY_UNKNOWN"
    missing_required: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class MorningEmailPayload:
    """Persisted prediction set projected for email delivery."""

    prediction_date: date
    generated_at: datetime
    cutoff_at: datetime
    candidates: tuple[EmailCandidate, ...]
    dashboard_url: str
    provider_status: str = "UNKNOWN"
    model_version: str = "unknown"
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    """Provider-neutral rendered message."""

    subject: str
    text: str
    html: str
    sender: str
    recipient: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class EmailDelivery:
    """Sanitized provider result."""

    provider: str
    message_id: str
    sent_at: datetime


class EmailSender(Protocol):
    """Boundary implemented by Gmail SMTP, Resend, and dry-run senders."""

    name: str

    def send(self, message: RenderedEmail) -> EmailDelivery:
        """Deliver one rendered email or raise a sanitized notification error."""


class EmailLogStore(Protocol):
    """Minimal persistence boundary used to prevent duplicate delivery."""

    def claim(self, idempotency_key: str) -> bool:
        """Atomically claim a key; return false when it already exists."""

    def mark_sent(self, idempotency_key: str, delivery: EmailDelivery) -> None:
        """Persist successful delivery metadata."""

    def mark_failed(self, idempotency_key: str, error: str) -> None:
        """Persist a sanitized failure for operational diagnosis."""

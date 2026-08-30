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
    # The forecast distribution, as ``(quantile, return)`` pairs in ascending
    # order. Plain tuples rather than the model type on purpose: this module
    # is the notification domain and must not drag scikit-learn into the mail
    # path. Empty when the day's prediction has no distribution, which the
    # template says out loud rather than rendering as a blank row.
    distribution: tuple[tuple[float, float], ...] = field(default_factory=tuple)
    # ``quantile_regression_l1`` for a fitted curve, ``residual_quantiles`` for
    # the constant-width fallback. Named in the mail because the two are not
    # equally trustworthy and must never look alike.
    distribution_method: str | None = None
    # Read off the curve at zero, and distinct from ``probability_up`` above,
    # which is the logistic classifier's answer and the one the buy rule uses.
    distribution_probability_up: float | None = None
    distribution_median: float | None = None
    # Probability mass per equal-width column of a shared axis, ready to
    # draw. Resampled in the service layer so the template never has to do
    # arithmetic on a distribution it is only supposed to display.
    density: tuple[float, ...] = field(default_factory=tuple)
    # The half-width of the axis ``density`` was sampled on, in return
    # terms. Carried with the samples so a row can never be drawn against
    # a ruler it was not measured on.
    density_scale: float | None = None
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

"""Provider-neutral morning prediction email support."""

from notifications.contracts import (
    EmailCandidate,
    EmailDelivery,
    EmailSender,
    MorningEmailPayload,
    RenderedEmail,
)
from notifications.service import EmailDispatcher
from notifications.templates import render_morning_email

__all__ = [
    "EmailCandidate",
    "EmailDelivery",
    "EmailDispatcher",
    "EmailSender",
    "MorningEmailPayload",
    "RenderedEmail",
    "render_morning_email",
]

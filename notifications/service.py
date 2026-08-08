"""Idempotent notification orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field

from notifications.contracts import (
    EmailDelivery,
    EmailLogStore,
    EmailSender,
    MorningEmailPayload,
)
from notifications.templates import render_morning_email


@dataclass(slots=True)
class InMemoryEmailLogStore:
    """Test/dry-run store; production injects the SQLAlchemy repository."""

    claimed: set[str] = field(default_factory=set)
    sent: dict[str, EmailDelivery] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)

    def claim(self, idempotency_key: str) -> bool:
        if idempotency_key in self.claimed:
            return False
        self.claimed.add(idempotency_key)
        return True

    def mark_sent(self, idempotency_key: str, delivery: EmailDelivery) -> None:
        self.sent[idempotency_key] = delivery

    def mark_failed(self, idempotency_key: str, error: str) -> None:
        self.failed[idempotency_key] = error


class EmailDispatcher:
    """Render, claim, send, and log one prediction email exactly once locally."""

    def __init__(self, sender: EmailSender, log_store: EmailLogStore) -> None:
        self._sender = sender
        self._log_store = log_store

    def dispatch(
        self,
        payload: MorningEmailPayload,
        *,
        sender_address: str,
        recipient: str,
        top_n: int = 5,
    ) -> EmailDelivery | None:
        message = render_morning_email(
            payload,
            sender=sender_address,
            recipient=recipient,
            top_n=top_n,
        )
        if not self._log_store.claim(message.idempotency_key):
            return None
        try:
            delivery = self._sender.send(message)
        except Exception as exc:
            self._log_store.mark_failed(message.idempotency_key, str(exc))
            raise
        self._log_store.mark_sent(message.idempotency_key, delivery)
        return delivery

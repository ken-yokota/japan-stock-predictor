"""Free Gmail SMTP and optional Resend email-provider implementations."""

from __future__ import annotations

import smtplib
import ssl
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import httpx

from notifications.contracts import EmailDelivery, RenderedEmail


class NotificationError(RuntimeError):
    """Sanitized notification failure safe for logs and UI."""


class GmailSmtpSender:
    """Send one personal notification through Gmail with STARTTLS."""

    name = "gmail_smtp"

    def __init__(
        self,
        *,
        username: str,
        app_password: str,
        host: str = "smtp.gmail.com",
        port: int = 587,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
    ) -> None:
        if not username.strip() or not app_password:
            raise ValueError("Gmail username and App Password are required")
        self._username = username
        self._app_password = app_password
        self._host = host
        self._port = port
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._backoff = backoff_seconds

    def send(self, message: RenderedEmail) -> EmailDelivery:
        email = EmailMessage()
        email["Subject"] = message.subject
        email["From"] = message.sender
        email["To"] = message.recipient
        email["X-Idempotency-Key"] = message.idempotency_key
        email.set_content(message.text)
        email.add_alternative(message.html, subtype="html")
        for attempt in range(self._max_retries + 1):
            try:
                with smtplib.SMTP(
                    self._host, self._port, timeout=self._timeout
                ) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                    smtp.login(self._username, self._app_password)
                    smtp.send_message(email)
                return EmailDelivery(
                    provider=self.name,
                    message_id=message.idempotency_key,
                    sent_at=datetime.now(UTC),
                )
            except (
                TimeoutError,
                OSError,
                smtplib.SMTPServerDisconnected,
                smtplib.SMTPConnectError,
            ) as exc:
                if attempt >= self._max_retries:
                    raise NotificationError("Gmail SMTP delivery failed") from exc
                time.sleep(self._backoff * (2**attempt))
            except smtplib.SMTPException as exc:
                raise NotificationError("Gmail SMTP rejected the message") from exc
        raise AssertionError("unreachable")


class ResendSender:
    """Optional API sender with provider-side idempotency support."""

    name = "resend"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Resend API key is required")
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def send(self, message: RenderedEmail) -> EmailDelivery:
        try:
            response = self._client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Idempotency-Key": message.idempotency_key,
                },
                json={
                    "from": message.sender,
                    "to": [message.recipient],
                    "subject": message.subject,
                    "text": message.text,
                    "html": message.html,
                },
            )
            response.raise_for_status()
            payload: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise NotificationError("Resend delivery failed") from exc
        message_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise NotificationError("Resend returned an invalid response")
        return EmailDelivery(self.name, message_id, datetime.now(UTC))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class DryRunSender:
    """Record no external side effect while exercising the full dispatcher."""

    name = "dry_run"

    def send(self, message: RenderedEmail) -> EmailDelivery:
        return EmailDelivery(self.name, message.idempotency_key, datetime.now(UTC))

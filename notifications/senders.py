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


def notification_error_summary(error: BaseException) -> str:
    """Preserve provider diagnostics without exposing arbitrary exception text."""

    return str(error) if isinstance(error, NotificationError) else type(error).__name__


def _smtp_failure(error: BaseException, stage: str) -> NotificationError:
    # SMTP response text can contain credentials or recipient addresses. Keep
    # only the operation, exception class and numeric server response code.
    code = getattr(error, "smtp_code", None)
    detail = type(error).__name__
    if isinstance(code, int):
        detail += f", smtp_code={code}"
    return NotificationError(f"Gmail SMTP {stage} failed ({detail})")


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
            stage = "connection"
            try:
                with smtplib.SMTP(
                    self._host, self._port, timeout=self._timeout
                ) as smtp:
                    smtp.ehlo()
                    stage = "TLS"
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                    stage = "authentication"
                    smtp.login(self._username, self._app_password)
                    stage = "delivery"
                    smtp.send_message(email)
                return EmailDelivery(
                    provider=self.name,
                    message_id=message.idempotency_key,
                    sent_at=datetime.now(UTC),
                )
            except smtplib.SMTPResponseException as exc:
                # SMTPException inherits OSError. Catch server responses first
                # so permanent authentication failures are not retried as I/O.
                if not 400 <= exc.smtp_code < 500 or attempt >= self._max_retries:
                    raise _smtp_failure(exc, stage) from None
            except smtplib.SMTPServerDisconnected as exc:
                if attempt >= self._max_retries:
                    raise _smtp_failure(exc, stage) from None
            except smtplib.SMTPException as exc:
                raise _smtp_failure(exc, stage) from None
            except ssl.SSLError as exc:
                raise _smtp_failure(exc, stage) from None
            except OSError as exc:
                if attempt >= self._max_retries:
                    raise _smtp_failure(exc, stage) from None
            time.sleep(self._backoff * (2**attempt))
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

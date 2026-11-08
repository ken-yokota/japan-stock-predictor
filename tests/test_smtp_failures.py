"""SMTP failures must remain actionable without exposing server response text."""

from __future__ import annotations

import smtplib
from typing import Any

import pytest

from notifications.contracts import RenderedEmail
from notifications.senders import (
    GmailSmtpSender,
    NotificationError,
    notification_error_summary,
)


def _message() -> RenderedEmail:
    return RenderedEmail(
        "TEST", "body", "<p>body</p>", "from@example.test", "to@example.test", "test"
    )


def test_authentication_failure_is_permanent_and_sanitized(monkeypatch: Any) -> None:
    attempts: list[int] = []

    class AuthenticationFailure:
        def __init__(self, *args: object, **kwargs: object) -> None:
            attempts.append(1)

        def __enter__(self) -> AuthenticationFailure:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def ehlo(self) -> None:
            pass

        def starttls(self, **kwargs: object) -> None:
            pass

        def login(self, *args: object) -> None:
            raise smtplib.SMTPAuthenticationError(
                535, b"private-password to@example.test"
            )

    monkeypatch.setattr("notifications.senders.smtplib.SMTP", AuthenticationFailure)
    sender = GmailSmtpSender(username="user", app_password="private-password")
    with pytest.raises(NotificationError) as failure:
        sender.send(_message())
    assert attempts == [1]
    assert "authentication" in str(failure.value)
    assert "smtp_code=535" in str(failure.value)
    assert "private-password" not in str(failure.value)
    assert "to@example.test" not in str(failure.value)
    assert failure.value.__suppress_context__


@pytest.mark.parametrize("code,expected_attempts", [(421, 3), (550, 1)])
def test_only_temporary_server_failures_are_retried(
    monkeypatch: Any, code: int, expected_attempts: int
) -> None:
    attempts: list[int] = []
    delays: list[float] = []

    def fail(*args: object, **kwargs: object) -> None:
        attempts.append(1)
        raise smtplib.SMTPConnectError(code, b"private provider details")

    monkeypatch.setattr("notifications.senders.smtplib.SMTP", fail)
    monkeypatch.setattr("notifications.senders.time.sleep", delays.append)
    sender = GmailSmtpSender(username="user", app_password="secret")
    with pytest.raises(NotificationError, match=f"smtp_code={code}"):
        sender.send(_message())
    assert len(attempts) == expected_attempts
    assert len(delays) == expected_attempts - 1


def test_connection_timeout_is_retried_without_private_details(
    monkeypatch: Any,
) -> None:
    attempts: list[int] = []

    def fail(*args: object, **kwargs: object) -> None:
        attempts.append(1)
        raise TimeoutError("private hostname")

    monkeypatch.setattr("notifications.senders.smtplib.SMTP", fail)
    monkeypatch.setattr("notifications.senders.time.sleep", lambda _: None)
    sender = GmailSmtpSender(username="user", app_password="secret")
    with pytest.raises(NotificationError) as failure:
        sender.send(_message())
    assert len(attempts) == 3
    assert "connection" in str(failure.value)
    assert "TimeoutError" in str(failure.value)
    assert "private hostname" not in str(failure.value)


def test_summary_preserves_only_deliberately_sanitized_errors() -> None:
    assert notification_error_summary(ValueError("private password")) == "ValueError"
    error = NotificationError("Gmail SMTP authentication failed (smtp_code=535)")
    assert notification_error_summary(error) == str(error)

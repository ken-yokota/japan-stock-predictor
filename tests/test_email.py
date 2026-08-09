from __future__ import annotations

from datetime import UTC, date, datetime
from email.message import EmailMessage
from types import TracebackType
from typing import Any, ClassVar

import httpx

from notifications.contracts import EmailCandidate, MorningEmailPayload
from notifications.senders import DryRunSender, GmailSmtpSender, ResendSender
from notifications.service import EmailDispatcher, InMemoryEmailLogStore
from notifications.templates import render_morning_email


def _payload(*candidates: EmailCandidate) -> MorningEmailPayload:
    return MorningEmailPayload(
        prediction_date=date(2026, 8, 10),
        generated_at=datetime(2026, 8, 9, 23, 35, tzinfo=UTC),
        cutoff_at=datetime(2026, 8, 9, 23, 30, tzinfo=UTC),
        candidates=tuple(candidates),
        dashboard_url="https://example.test/dashboard?x=1&y=2",
        provider_status="PARTIAL",
        model_version="ridge-v1",
        warnings=("USDJPY is stale",),
    )


def test_template_renders_buy_candidate_and_escapes_html() -> None:
    candidate = EmailCandidate(
        ticker="1605",
        company="INPEX <test>",
        predicted_return=0.012,
        probability_up=0.74,
        signal="BUY",
        readability_score=89,
        profit_factor=2.21,
        expectancy_jpy=8400,
        positive_factors=("WTI",),
        negative_factors=("VIX",),
    )
    message = render_morning_email(
        _payload(candidate), sender="sender@example.com", recipient="me@example.com"
    )
    assert "INPEX <test>" in message.text
    assert "INPEX &lt;test&gt;" in message.html
    assert "+1.20%" in message.text
    assert "investment" not in message.text.lower()
    assert message.idempotency_key.startswith("morning/2026-08-10/")


def test_template_explicitly_reports_no_buy_candidates() -> None:
    candidate = EmailCandidate(
        ticker="7203",
        company="Toyota",
        predicted_return=0.001,
        probability_up=0.55,
        signal="NO_BUY",
    )
    message = render_morning_email(
        _payload(candidate), sender="sender@example.com", recipient="me@example.com"
    )
    assert "本日は条件を満たすBUY候補なし" in message.text
    assert "USDJPY is stale" in message.text


def test_dispatcher_prevents_duplicate_delivery() -> None:
    store = InMemoryEmailLogStore()
    dispatcher = EmailDispatcher(DryRunSender(), store)
    payload = _payload()
    first = dispatcher.dispatch(
        payload, sender_address="sender@example.com", recipient="me@example.com"
    )
    second = dispatcher.dispatch(
        payload, sender_address="sender@example.com", recipient="me@example.com"
    )
    assert first is not None
    assert second is None
    assert len(store.sent) == 1


class _FakeSmtp:
    messages: ClassVar[list[EmailMessage]] = []
    credentials: ClassVar[tuple[str, str] | None] = None
    tls_started: ClassVar[bool] = False

    def __init__(self, host: str, port: int, *, timeout: float) -> None:
        assert host == "smtp.gmail.com"
        assert port == 587
        assert timeout == 20.0

    def __enter__(self) -> _FakeSmtp:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def starttls(self, *, context: Any) -> None:
        assert context is not None
        self.__class__.tls_started = True

    def login(self, username: str, password: str) -> None:
        self.__class__.credentials = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        self.__class__.messages.append(message)


def test_gmail_sender_uses_starttls_and_app_password(monkeypatch: Any) -> None:
    monkeypatch.setattr("notifications.senders.smtplib.SMTP", _FakeSmtp)
    sender = GmailSmtpSender(username="sender@gmail.com", app_password="app-secret")
    message = render_morning_email(
        _payload(), sender="sender@gmail.com", recipient="me@example.com"
    )
    result = sender.send(message)
    assert result.provider == "gmail_smtp"
    assert _FakeSmtp.tls_started
    assert _FakeSmtp.credentials == ("sender@gmail.com", "app-secret")
    assert _FakeSmtp.messages[-1]["X-Idempotency-Key"] == message.idempotency_key


def test_resend_sender_sets_idempotency_header() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(request.headers))
        return httpx.Response(200, json={"id": "email_123"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sender = ResendSender("api-secret", client=client)
    message = render_morning_email(
        _payload(), sender="sender@example.com", recipient="me@example.com"
    )
    result = sender.send(message)
    assert result.message_id == "email_123"
    assert seen_headers["idempotency-key"] == message.idempotency_key
    client.close()


def test_a_missing_prediction_is_mailed_rather_than_raised(monkeypatch) -> None:
    """An unsent mail and a crashed process look identical from a phone.

    The morning job can fail for reasons unrelated to this script. When it
    does, "no prediction today" is exactly the message worth delivering, and
    the reader is usually away from the machine.
    """

    import scripts.send_morning_email as script

    sent: list[object] = []

    class _Environment:
        def require_email_addresses(self):
            return ("from@example.com", "to@example.com")

    monkeypatch.setattr(
        script,
        "_sender",
        lambda environment: type(
            "S", (), {"send": lambda self, message: sent.append(message)}
        )(),
    )
    script._notify_missing(_Environment(), date(2026, 8, 10))

    assert len(sent) == 1
    assert "2026-08-10" in sent[0].subject
    # The job fires three times by design; one notice per date, not three.
    assert sent[0].idempotency_key == "missing-prediction/2026-08-10"


def test_the_missing_prediction_notice_never_raises(monkeypatch) -> None:
    """This path runs when something is already wrong.

    A failure here must not replace one silent morning with a louder one, so a
    broken sender is reported on stdout and swallowed.
    """

    import scripts.send_morning_email as script

    class _Environment:
        def require_email_addresses(self):
            raise RuntimeError("credentials missing")

    script._notify_missing(_Environment(), None)

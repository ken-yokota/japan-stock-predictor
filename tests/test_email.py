from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import Any, ClassVar

import httpx
import yaml

from notifications.contracts import EmailCandidate, EmailDelivery, MorningEmailPayload
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
            "S",
            (),
            {
                "send": lambda self, message: (
                    sent.append(message)
                    or EmailDelivery("fake", "missing-notice-1", datetime.now(UTC))
                )
            },
        )(),
    )
    result = script._notify_missing(_Environment(), date(2026, 8, 10))

    assert len(sent) == 1
    assert "2026-08-10" in sent[0].subject
    assert sent[0].idempotency_key == "missing-prediction/2026-08-10"
    assert result == {
        "notification_status": "SENT",
        "notification_provider": "fake",
        "notification_message_id": "missing-notice-1",
        "notification_error_type": None,
    }


def test_the_missing_prediction_notice_reports_missing_credentials(monkeypatch) -> None:
    """This path runs when something is already wrong.

    A failure here is returned in sanitized form so main can make Actions red.
    """

    import scripts.send_morning_email as script

    class _Environment:
        def require_email_addresses(self):
            raise RuntimeError("credentials missing")

    result = script._notify_missing(_Environment(), date(2026, 8, 10))

    assert result["notification_status"] == "FAILED"
    assert result["notification_error_type"] == "RuntimeError"
    assert "credentials missing" not in result.values()


def test_only_the_last_scheduled_firing_reports_a_missing_prediction() -> None:
    """Three firings must not become three identical mails.

    The schedule fires 08:45, 08:50, and 08:55 from one cron expression, so the
    process cannot tell them apart, and the database cannot deduplicate this
    notice: email_logs.prediction_set_id is NOT NULL and there is no set to
    point at. The clock is what is left.
    """

    from datetime import datetime
    from zoneinfo import ZoneInfo

    from scripts.send_morning_email import _should_notify

    jst = ZoneInfo("Asia/Tokyo")
    fired = [
        _should_notify(None, datetime(2026, 8, 10, 8, minute, tzinfo=jst))
        for minute in (45, 50, 55)
    ]
    assert fired == [False, False, True]


def test_an_explicitly_requested_date_always_reports() -> None:
    """Someone ran it on purpose; silence would look like a hang."""

    from datetime import datetime
    from zoneinfo import ZoneInfo

    from scripts.send_morning_email import _should_notify

    early = datetime(2026, 8, 10, 8, 45, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert _should_notify(date(2026, 8, 10), early) is True


def test_an_early_scheduled_attempt_stays_deferred_even_when_actions_is_late() -> None:
    """Cron identity, not delayed runner wall time, selects the final notice."""

    from datetime import datetime
    from zoneinfo import ZoneInfo

    from scripts.send_morning_email import _should_notify

    delayed = datetime(2026, 8, 10, 9, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert _should_notify(None, delayed, defer_missing=True) is False


class _DisposableEngine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


def _prepare_main(
    monkeypatch: Any,
    *,
    explicit_date: bool = True,
    business_day: bool = True,
) -> tuple[object, object, _DisposableEngine]:
    """Give the CLI deterministic runtime collaborators without touching a DB."""

    import scripts.send_morning_email as script

    config = object()
    environment = SimpleNamespace(app_url="https://dashboard.example.test")
    engine = _DisposableEngine()
    factory = object()
    argv = ["send_morning_email"]
    if explicit_date:
        argv.extend(["--prediction-date", "2026-08-10"])
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr(
        script,
        "load_runtime",
        lambda config_dir: (config, environment, engine, factory),
    )
    monkeypatch.setattr(
        script,
        "today_in_application_timezone",
        lambda loaded_config: date(2026, 8, 10),
    )
    monkeypatch.setattr(script, "is_japan_business_day", lambda target: business_day)
    monkeypatch.setattr(script, "_validate_delivery_configuration", lambda env: None)
    return environment, factory, engine


def _last_result(capsys: Any) -> dict[str, object]:
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines
    return json.loads(lines[-1])


def test_final_missing_prediction_notice_is_red_even_when_delivered(
    monkeypatch: Any, capsys: Any
) -> None:
    """Mail delivery succeeded, but the requested prediction still did not."""

    import scripts.send_morning_email as script

    _prepare_main(monkeypatch)
    monkeypatch.setattr(
        script,
        "send_persisted_morning_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("no terminal prediction set is available for 2026-08-10")
        ),
    )
    monkeypatch.setattr(
        script,
        "_notify_missing",
        lambda environment, target: {
            "notification_status": "SENT",
            "notification_provider": "fake",
            "notification_message_id": "notice-1",
            "notification_error_type": None,
        },
    )

    exit_code = script.main()
    result = _last_result(capsys)

    assert exit_code == script.EXIT_NO_PREDICTION == 2
    assert result["status"] == "FAILED"
    assert result["reason"] == "NO_PREDICTION_SET"
    assert result["notification_status"] == "SENT"
    assert result["exit_code"] == 2


def test_missing_delivery_credentials_exit_three_before_database_claim(
    monkeypatch: Any, capsys: Any
) -> None:
    """A bad sender must not leave a claimed row that blocks all three retries."""

    import scripts.send_morning_email as script

    _prepare_main(monkeypatch)
    monkeypatch.setattr(
        script,
        "_validate_delivery_configuration",
        lambda environment: (_ for _ in ()).throw(ValueError("credentials missing")),
    )
    monkeypatch.setattr(
        script,
        "send_persisted_morning_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("configuration must fail before the database claim")
        ),
    )

    exit_code = script.main()
    result = _last_result(capsys)

    assert exit_code == script.EXIT_NOTIFICATION_FAILED == 3
    assert result["status"] == "FAILED"
    assert result["reason"] == "EMAIL_CONFIGURATION_FAILED"
    assert result["error_type"] == "ValueError"
    assert "credentials missing" not in json.dumps(result)


def test_missing_prediction_notice_smtp_failure_is_red(
    monkeypatch: Any, capsys: Any
) -> None:
    """A failed fallback is visible as exit 3, never a green Actions run."""

    import scripts.send_morning_email as script

    _prepare_main(monkeypatch)
    monkeypatch.setattr(
        script,
        "send_persisted_morning_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("no terminal prediction set is available for 2026-08-10")
        ),
    )
    monkeypatch.setattr(
        script,
        "_notify_missing",
        lambda environment, target: {
            "notification_status": "FAILED",
            "notification_provider": None,
            "notification_message_id": None,
            "notification_error_type": "NotificationError",
        },
    )

    exit_code = script.main()
    result = _last_result(capsys)

    assert exit_code == script.EXIT_NOTIFICATION_FAILED == 3
    assert result["status"] == "FAILED"
    assert result["reason"] == "NO_PREDICTION_SET"
    assert result["notification_status"] == "FAILED"
    assert result["notification_error_type"] == "NotificationError"


def test_early_missing_prediction_attempt_stays_retryable(
    monkeypatch: Any, capsys: Any
) -> None:
    """The first two cron firings wait; only the final one pages and fails."""

    import scripts.send_morning_email as script

    _prepare_main(monkeypatch, explicit_date=False)
    monkeypatch.setattr(script, "_should_notify", lambda explicit_date, **kwargs: False)
    monkeypatch.setattr(
        script,
        "send_persisted_morning_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("no terminal prediction set is available for 2026-08-10")
        ),
    )
    monkeypatch.setattr(
        script,
        "_notify_missing",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("early retry must not send the fallback notice")
        ),
    )

    exit_code = script.main()
    result = _last_result(capsys)

    assert exit_code == 0
    assert result["status"] == "RETRY_PENDING"
    assert result["notification_status"] == "DEFERRED"


def test_duplicate_delivery_is_an_explicit_success(
    monkeypatch: Any, capsys: Any
) -> None:
    """A DB idempotency claim held by a prior run means no second email."""

    import scripts.send_morning_email as script

    _prepare_main(monkeypatch)
    monkeypatch.setattr(script, "send_persisted_morning_email", lambda *a, **k: None)

    exit_code = script.main()
    result = _last_result(capsys)

    assert exit_code == 0
    assert result["status"] == "SUCCESS"
    assert result["outcome"] == "ALREADY_SENT"


def test_non_business_day_skips_without_querying_or_sending(
    monkeypatch: Any, capsys: Any
) -> None:
    """A JPX holiday is expected silence, not a missing-prediction incident."""

    import scripts.send_morning_email as script

    _prepare_main(monkeypatch, business_day=False)
    monkeypatch.setattr(
        script,
        "send_persisted_morning_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("non-business days must not query or send")
        ),
    )

    exit_code = script.main()
    result = _last_result(capsys)

    assert exit_code == 0
    assert result == {
        "status": "SKIPPED",
        "reason": "NON_BUSINESS_DAY",
        "prediction_date": "2026-08-10",
        "exit_code": 0,
    }


def test_workflows_keep_retries_and_fail_only_required_close_attempt() -> None:
    """Pin the Actions policy that distinguishes retry-pending from false green."""

    workflow_dir = Path(__file__).parents[1] / ".github" / "workflows"
    morning_text = (workflow_dir / "morning_email.yml").read_text(encoding="utf-8")
    morning = yaml.load(morning_text, Loader=yaml.BaseLoader)
    assert morning["on"]["schedule"] == [
        {"cron": "45 23 * * 0-4"},
        {"cron": "50 23 * * 0-4"},
        {"cron": "55 23 * * 0-4"},
    ]
    assert "DEFER_MISSING" in morning["jobs"]["email"]["env"]
    assert "args+=(--defer-missing)" in morning["jobs"]["email"]["steps"][-1]["run"]

    prediction = yaml.load(
        (workflow_dir / "morning_prediction.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert prediction["on"]["schedule"] == [{"cron": "10,20,30 23 * * 0-4"}]
    assert prediction["jobs"]["predict"]["timeout-minutes"] == "90"
    prediction_inputs = prediction["on"]["workflow_dispatch"]["inputs"]
    assert prediction_inputs["skip_ingestion"]["default"] == "false"
    prediction_steps = prediction["jobs"]["predict"]["steps"]
    wait_step = next(
        step for step in prediction_steps if step.get("name", "").startswith("Wait ")
    )
    assert wait_step["if"] == "github.event_name == 'schedule'"
    wait_run = wait_step["run"]
    assert "15 * 60" in wait_run
    assert "max(0, math.ceil((target - now).total_seconds()))" in wait_run
    assert (
        subprocess.run(
            ["bash", "-n"], input=wait_run, text=True, check=False
        ).returncode
        == 0
    )
    python_source = wait_run.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    compile(python_source, "morning_prediction_wait_step", "exec")
    build_step = next(
        step
        for step in prediction_steps
        if step.get("name") == "Build morning predictions"
    )
    assert build_step["timeout-minutes"] == "20"
    assert "args+=(--skip-ingestion)" in build_step["run"]

    close_text = (workflow_dir / "close_update.yml").read_text(encoding="utf-8")
    close = yaml.load(close_text, Loader=yaml.BaseLoader)
    requirement = close["jobs"]["close"]["env"]["REQUIRE_COMPLETE"]
    assert "workflow_dispatch" in requirement
    assert "10 7 * * 1-5" in requirement
    run = close["jobs"]["close"]["steps"][-1]["run"]
    assert "PARTIAL|NO_PREDICTION_SET" in run
    assert '[[ "$REQUIRE_COMPLETE" == "true" ]]' in run
    assert "exit 2" in run


def test_close_workflow_policy_executes_early_retry_and_final_failure(
    tmp_path: Path,
) -> None:
    """Execute the Actions shell with a fake pipeline, not just text-match it."""

    workflow_path = Path(__file__).parents[1] / ".github/workflows/close_update.yml"
    workflow = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)
    run = workflow["jobs"]["close"]["steps"][-1]["run"]

    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-m" ]; then\n'
        '  printf \'%s\\n\' "{\\"status\\":\\"$FAKE_CLOSE_STATUS\\"}"\n'
        '  exit "${FAKE_CLOSE_EXIT:-0}"\n'
        "fi\n"
        'exec "$REAL_PYTHON" "$@"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    def execute(
        status: str, *, require_complete: bool
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{tmp_path}:{environment['PATH']}",
                "REAL_PYTHON": sys.executable,
                "FAKE_CLOSE_STATUS": status,
                "PREDICTION_DATE": "",
                "DRY_RUN": "false",
                "REQUIRE_COMPLETE": str(require_complete).lower(),
            }
        )
        return subprocess.run(
            ["bash"],
            input=run,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )

    early = execute("PARTIAL", require_complete=False)
    final = execute("PARTIAL", require_complete=True)
    missing = execute("NO_PREDICTION_SET", require_complete=True)
    successful = execute("SUCCESS", require_complete=True)

    assert early.returncode == 0
    assert "Close update retry pending" in early.stdout
    assert final.returncode == 2
    assert "Close update incomplete" in final.stdout
    assert missing.returncode == 2
    assert "NO_PREDICTION_SET" in missing.stdout
    assert successful.returncode == 0
    assert "Close update status: SUCCESS" in successful.stdout


def test_status_report_renders_tables_and_badges_from_a_description() -> None:
    """Progress mails go through the same layout as the prediction mail."""

    from scripts.send_status_report import plain_text, render_status_report

    report = {
        "title": "進捗報告",
        "lede": "工程 2/4 実行中",
        "sections": [
            {
                "title": "いま進めているタスク",
                "headers": [["工程", "center"], ["内容", "left"]],
                "rows": [
                    [
                        {"text": "2/4", "align": "center"},
                        {"text": "本番でのフル実行"},
                        {"badge": "実行中", "tone": "now"},
                    ]
                ],
                "note": "経過22分。想定を超えています。",
            }
        ],
        "footer": "操作は不要です。",
    }

    html_body = render_status_report(report)

    assert "<table" in html_body
    assert "本番でのフル実行" in html_body
    assert "実行中" in html_body
    assert "経過22分" in html_body
    # The tables must be able to scroll on their own, never the page body.
    assert "overflow-x:auto" in html_body

    text_body = plain_text(report)
    assert "■ いま進めているタスク" in text_body
    assert "2/4 / 本番でのフル実行 / 実行中" in text_body


def test_status_report_survives_a_section_with_no_rows() -> None:
    """An empty section must render its title, not raise."""

    from scripts.send_status_report import render_status_report

    html_body = render_status_report(
        {"title": "t", "sections": [{"title": "残タスク", "note": "なし"}]}
    )

    assert "残タスク" in html_body
    assert "なし" in html_body


def _snapshot(**overrides: object):
    from scripts.send_progress_report import Snapshot

    return Snapshot(**overrides)  # type: ignore[arg-type]


def test_progress_report_states_what_it_could_not_read() -> None:
    """A failed read is named, never rendered as a dash that looks like zero."""

    from scripts.send_progress_report import build_report, render

    report = build_report(
        _snapshot(errors=("データベース参照に失敗: OperationalError",)), None, 30
    )

    html_body = render(report)
    assert "この報告で取得できなかったもの" in html_body
    assert "OperationalError" in html_body
    assert "本番状態を取得できませんでした" in report["lede"]


def test_progress_report_flags_a_stale_task_note(tmp_path: Path) -> None:
    """An old note must be labelled old, not presented as the current work."""

    import os
    import time

    from scripts.send_progress_report import build_report, render

    note = tmp_path / "tasks.json"
    note.write_text(
        json.dumps({"tasks": [{"step": "1/2", "title": "古い作業"}]}),
        encoding="utf-8",
    )
    old = time.time() - 3600
    os.utime(note, (old, old))

    fresh = render(build_report(_snapshot(), note, 300))
    stale = render(build_report(_snapshot(), note, 30))

    assert "古い作業" in fresh
    assert "更新されていません" not in fresh
    assert "更新されていません" in stale


def test_progress_report_lists_what_happens_next() -> None:
    """The mail answers "今後" without the operator opening the app."""

    from scripts.send_progress_report import build_report, render

    html_body = render(build_report(_snapshot(), None, 30))

    assert "今後の予定" in html_body
    assert any(clock in html_body for clock in ("07:15", "08:20", "08:45", "16:10"))


def test_progress_report_plain_text_carries_the_same_facts() -> None:
    """No client is left with an empty body, and the numbers still appear."""

    from scripts.send_progress_report import build_report, plain_text

    snapshot = _snapshot(
        prediction_date="2026-08-11",
        prediction_status="READY",
        buys=3,
        settled=("2026-08-10", 5, 3, 14042.5),
        database_size="381 MB",
    )

    body = plain_text(snapshot, build_report(snapshot, None, 30))

    assert "2026-08-11" in body
    assert "3/5的中" in body
    assert "+14,042円" in body
    assert "381 MB" in body

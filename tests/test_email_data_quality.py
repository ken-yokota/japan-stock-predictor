"""The morning mail has to say when a BUY was built on incomplete inputs.

A recommendation that reads identically whether or not its required indicators
arrived is the failure mode this whole line of work exists to close, and the
mail is where the operator actually looks.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from notifications.contracts import EmailCandidate, MorningEmailPayload
from notifications.templates import render_morning_email

WHEN = datetime(2026, 8, 13, 8, 30, tzinfo=UTC)


def _candidate(
    ticker: str,
    *,
    signal: str = "BUY",
    quality: str = "CLEAN",
    missing: tuple[str, ...] = (),
) -> EmailCandidate:
    return EmailCandidate(
        ticker=ticker,
        company=f"会社{ticker}",
        predicted_return=0.0062,
        probability_up=0.67,
        signal=signal,
        rank=1,
        data_quality=quality,
        missing_required=missing,
    )


def _render(*candidates: EmailCandidate):  # type: ignore[no-untyped-def]
    payload = MorningEmailPayload(
        prediction_date=date(2026, 8, 13),
        generated_at=WHEN,
        cutoff_at=WHEN,
        candidates=candidates,
        dashboard_url="https://example.invalid/app",
    )
    return render_morning_email(
        payload, sender="a@example.invalid", recipient="b@example.invalid"
    )


def test_a_clean_buy_reports_the_quality_counts() -> None:
    rendered = _render(_candidate("9107"), _candidate("9101", signal="HOLD"))
    assert "CLEAN 2" in rendered.text
    assert "DEGRADED 0" in rendered.text
    assert "必須指標が欠けた状態" not in rendered.text


def test_a_degraded_buy_is_named_in_the_message() -> None:
    rendered = _render(
        _candidate("9107"),
        _candidate("9101", quality="DEGRADED", missing=("usdjpy", "audjpy")),
    )
    assert "DEGRADED 1" in rendered.text
    assert "9101" in rendered.text
    assert "usdjpy" in rendered.text
    assert "必須指標が欠けた状態" in rendered.text


def test_a_legacy_day_is_not_reported_as_clean() -> None:
    rendered = _render(_candidate("9107", quality="LEGACY_UNKNOWN"))
    assert "UNKNOWN 1" in rendered.text
    assert "CLEAN 0" in rendered.text


def test_no_buy_still_reports_that_the_system_ran() -> None:
    rendered = _render(_candidate("9107", signal="HOLD"))
    assert "本日は条件を満たすBUY候補なし" in rendered.text
    assert "BUYなしと予測失敗は別" in rendered.text
    assert "データ品質" in rendered.text


def test_every_missing_required_indicator_is_counted() -> None:
    rendered = _render(
        _candidate("9101", quality="DEGRADED", missing=("usdjpy",)),
        _candidate("9104", quality="DEGRADED", missing=("usdjpy", "eurjpy")),
    )
    assert "usdjpy（2銘柄）" in rendered.text
    assert "eurjpy（1銘柄）" in rendered.text


def test_the_dashboard_url_survives() -> None:
    rendered = _render(_candidate("9107"))
    assert "https://example.invalid/app" in rendered.html

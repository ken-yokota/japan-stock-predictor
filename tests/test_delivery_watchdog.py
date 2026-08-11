"""The watchdog exists so a missed send is loud rather than invisible."""

from __future__ import annotations

from datetime import date

from scripts.verify_daily_delivery import Check, Outcome, _alert_bodies


def _outcome(**results: bool) -> Outcome:
    return Outcome(
        window="morning",
        for_date=date(2026, 8, 12),
        checks=[Check(name, ok, "detail") for name, ok in results.items()],
    )


def test_all_green_is_not_an_alert() -> None:
    outcome = _outcome(prediction=True, email=True, dashboard=True)
    assert outcome.ok
    assert outcome.failures == []


def test_a_single_missing_piece_fails_the_window() -> None:
    outcome = _outcome(prediction=True, email=False, dashboard=True)
    assert not outcome.ok
    assert [check.name for check in outcome.failures] == ["email"]


def test_the_subject_names_what_is_missing() -> None:
    """The operator reads the subject on a phone; it has to carry the fact."""

    subject, text_body, html_body = _alert_bodies(
        _outcome(prediction=True, email=False, dashboard=False)
    )
    assert "email" in subject and "dashboard" in subject
    assert "要確認" in subject
    # Every check appears in the body, passing ones included, so the reader can
    # see what was verified rather than only what broke.
    for name in ("prediction", "email", "dashboard"):
        assert name in text_body
        assert name in html_body
    assert "NG" in text_body and "OK" in text_body

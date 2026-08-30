"""Project persisted predictions into one idempotent morning email."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from data.config import AppConfig
from data.env import EnvironmentSettings
from database.models import (
    DailyRun,
    MetricSnapshot,
    ModelCoefficient,
    Prediction,
    PredictionSet,
)
from database.repository import PredictionPipelineRepository
from models.distribution import ReturnDistribution
from notifications.contracts import EmailCandidate, EmailDelivery, MorningEmailPayload
from notifications.method_thresholds import load_thresholds
from notifications.senders import (
    DryRunSender,
    GmailSmtpSender,
    NotificationError,
    ResendSender,
)
from notifications.templates import DENSITY_COLUMNS, render_morning_email

# Bumped when the mail stopped leading with a point forecast and started
# leading with the distribution. The version is part of the delivery record,
# so a reader of email_logs can tell which shape of mail actually went out.
TEMPLATE_VERSION = "morning-v2-distribution"


def _distribution(row: Prediction) -> ReturnDistribution | None:
    """Rebuild a persisted curve, or ``None`` for a row that has none.

    Never raises: a malformed or partial document is a reason to send the
    mail without a distribution for that ticker -- which the template says
    out loud -- not a reason to send no mail at all.
    """

    payload = row.return_distribution
    if not payload:
        return None
    try:
        return ReturnDistribution.from_payload(payload)
    except Exception:
        return None


def _density_scale(curves: Sequence[ReturnDistribution]) -> float:
    """One axis half-width for the whole message, from the widest forecast.

    Every density in a mail is sampled on this same axis. Sampling each ticker
    on its own axis would silently rescale the picture per row, so a forecast
    twice as uncertain as another would be drawn the same width -- which is
    exactly the comparison the operator is looking at the figure to make.
    """

    widest = [
        abs(value) for curve in curves for value in (curve.values[0], curve.values[-1])
    ]
    return max(widest) if widest else 0.0


def _trim_warnings(
    values: list[str] | tuple[str, ...], *, limit: int = 8
) -> tuple[str, ...]:
    unique = tuple(dict.fromkeys(value for value in values if value))
    if len(unique) <= limit:
        return unique
    return (*unique[:limit], f"ほか{len(unique) - limit}件")


def _latest_metric(
    session: Session, ticker: str, as_of_date: date
) -> MetricSnapshot | None:
    return session.scalar(
        select(MetricSnapshot)
        .where(
            MetricSnapshot.ticker == ticker,
            MetricSnapshot.as_of_date <= as_of_date,
        )
        .order_by(MetricSnapshot.as_of_date.desc(), MetricSnapshot.computed_at.desc())
        .limit(1)
    )


def _factors(
    session: Session, model_run_id: str | None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if model_run_id is None:
        return (), ()
    rows = list(
        session.scalars(
            select(ModelCoefficient).where(
                ModelCoefficient.model_run_id == model_run_id
            )
        )
    )
    positive = tuple(
        row.feature_name
        for row in sorted(rows, key=lambda row: row.coefficient, reverse=True)
        if row.coefficient > 0
    )[:3]
    negative = tuple(
        row.feature_name
        for row in sorted(rows, key=lambda row: row.coefficient)
        if row.coefficient < 0
    )[:3]
    return positive, negative


def load_morning_email_payload(
    session: Session,
    config: AppConfig,
    *,
    prediction_date: date | None,
    dashboard_url: str,
    prediction_set_id: str | None = None,
) -> tuple[PredictionSet, MorningEmailPayload]:
    """Read only a terminal persisted set; never calculate or fetch here.

    ``prediction_set_id`` names one set exactly and bypasses the MORNING
    filter. It exists for previewing a message that will never be delivered on
    its own -- a reference or replayed set, which the scheduled path must keep
    refusing. The alternative was flipping a run's type in production to make
    it visible and flipping it back, and a temporary write to the live record
    to render a preview is not a trade worth making: the close pipeline scores
    whichever MORNING set is newest, so a crash between the two writes would
    put a replay into the live record.
    """

    if prediction_set_id is not None:
        chosen = session.get(PredictionSet, prediction_set_id)
        if chosen is None or chosen.status not in ("READY", "INSUFFICIENT_DATA"):
            raise ValueError(
                f"no terminal prediction set is available for {prediction_set_id}"
            )
        return _project_prediction_set(session, config, chosen, dashboard_url)

    # A reference prediction is never mailed on the scheduled path: it names a
    # session that does not open, and a message that looks like every other
    # morning would be read as one.
    statement = (
        select(PredictionSet)
        .join(DailyRun, DailyRun.run_id == PredictionSet.run_id)
        .where(
            PredictionSet.status.in_(("READY", "INSUFFICIENT_DATA")),
            DailyRun.run_type == "MORNING",
        )
    )
    if prediction_date is not None:
        statement = statement.where(PredictionSet.prediction_date == prediction_date)
    prediction_set = session.scalar(
        statement.order_by(
            PredictionSet.prediction_date.desc(), PredictionSet.generated_at.desc()
        ).limit(1)
    )
    if prediction_set is None:
        label = prediction_date.isoformat() if prediction_date else "latest"
        raise ValueError(f"no terminal prediction set is available for {label}")
    return _project_prediction_set(session, config, prediction_set, dashboard_url)


def _project_prediction_set(
    session: Session,
    config: AppConfig,
    prediction_set: PredictionSet,
    dashboard_url: str,
) -> tuple[PredictionSet, MorningEmailPayload]:
    """Turn one persisted set into the payload the templates render."""

    names = {stock.ticker: stock.name for stock in config.stocks.stocks}
    rows = list(
        session.scalars(
            select(Prediction)
            .where(Prediction.prediction_set_id == prediction_set.prediction_set_id)
            .order_by(Prediction.rank.asc().nulls_last(), Prediction.ticker)
        )
    )
    curves = {row.ticker: _distribution(row) for row in rows}
    scale = _density_scale([c for c in curves.values() if c is not None])
    candidates: list[EmailCandidate] = []
    for row in rows:
        metric = _latest_metric(session, row.ticker, prediction_set.prediction_date)
        positive, negative = _factors(session, row.regression_model_run_id)
        distribution = curves[row.ticker]
        density: tuple[float, ...] = ()
        if distribution is not None and scale > 0.0:
            density = distribution.density_profile(-scale, scale, DENSITY_COLUMNS)
        candidates.append(
            EmailCandidate(
                ticker=row.ticker,
                company=names.get(row.ticker, row.ticker),
                predicted_return=(
                    float(row.predicted_intraday_return)
                    if row.predicted_intraday_return is not None
                    else None
                ),
                probability_up=(
                    float(row.probability_up)
                    if row.probability_up is not None
                    else None
                ),
                signal=row.signal,
                status="READY" if row.status == "SUCCESS" else row.status,
                reference_price=(
                    float(row.reference_price)
                    if row.reference_price is not None
                    else None
                ),
                predicted_close=(
                    float(row.predicted_close)
                    if row.predicted_close is not None
                    else None
                ),
                rank=row.rank,
                readability_score=(
                    float(metric.readability_score)
                    if metric is not None and metric.readability_score is not None
                    else None
                ),
                profit_factor=(
                    float(metric.profit_factor)
                    if metric is not None and metric.profit_factor is not None
                    else None
                ),
                expectancy_jpy=(
                    float(metric.expectancy_jpy)
                    if metric is not None and metric.expectancy_jpy is not None
                    else None
                ),
                distribution=(distribution.pairs() if distribution is not None else ()),
                distribution_method=(
                    distribution.method if distribution is not None else None
                ),
                distribution_probability_up=(
                    distribution.probability_above(0.0)
                    if distribution is not None
                    else None
                ),
                distribution_median=(
                    distribution.median if distribution is not None else None
                ),
                arms=tuple(row.arm_predictions or ()),
                density=density,
                density_scale=scale if density else None,
                positive_factors=positive,
                negative_factors=negative,
                warnings=_trim_warnings(row.warnings, limit=3),
            )
        )
    run = session.get(DailyRun, prediction_set.run_id)
    provider_status = run.status if run is not None else "UNKNOWN"
    payload = MorningEmailPayload(
        prediction_date=prediction_set.prediction_date,
        generated_at=prediction_set.generated_at,
        cutoff_at=prediction_set.cutoff_at,
        candidates=tuple(candidates),
        dashboard_url=dashboard_url,
        provider_status=provider_status,
        model_version=prediction_set.model_version,
        warnings=_trim_warnings(prediction_set.warnings),
        method_thresholds=load_thresholds(),
    )
    return prediction_set, payload


def _sender(
    environment: EnvironmentSettings,
) -> GmailSmtpSender | ResendSender | DryRunSender:
    if environment.email_provider == "dry_run":
        return DryRunSender()
    if environment.email_provider == "resend":
        return ResendSender(
            environment.require_resend_key(),
            timeout_seconds=environment.http_timeout_seconds,
        )
    username, password = environment.require_gmail_credentials()
    return GmailSmtpSender(
        username=username,
        app_password=password,
        host=environment.smtp_host,
        port=environment.smtp_port,
        timeout_seconds=environment.http_timeout_seconds,
        max_retries=environment.http_max_retries,
    )


def send_persisted_morning_email(
    factory: sessionmaker[Session],
    config: AppConfig,
    environment: EnvironmentSettings,
    *,
    prediction_date: date | None = None,
    top_n: int = 5,
) -> EmailDelivery | None:
    """Claim in DB, commit, send, then record the provider result.

    The claim is committed before SMTP/API I/O so concurrent workflow retries
    cannot both send.  A process crash after SMTP acceptance intentionally
    leaves ``SENDING`` for manual review instead of risking a duplicate.
    """

    sender_address, recipient = environment.require_email_addresses()
    with factory() as session:
        prediction_set, payload = load_morning_email_payload(
            session,
            config,
            prediction_date=prediction_date,
            dashboard_url=environment.app_url,
        )
        message = render_morning_email(
            payload,
            sender=sender_address,
            recipient=recipient,
            top_n=top_n,
        )
        repository = PredictionPipelineRepository(session)
        repository.create_email_log(
            prediction_set_id=prediction_set.prediction_set_id,
            recipient=recipient,
            template_version=TEMPLATE_VERSION,
            subject=message.subject,
            idempotency_key=message.idempotency_key,
        )
        session.commit()

    with factory() as session:
        claimed = PredictionPipelineRepository(session).claim_email(
            message.idempotency_key
        )
        session.commit()
    if not claimed:
        return None

    email_sender = _sender(environment)
    try:
        delivery = email_sender.send(message)
    except Exception as exc:
        safe_error = (
            str(exc) if isinstance(exc, NotificationError) else type(exc).__name__
        )
        with factory() as session:
            PredictionPipelineRepository(session).mark_email_failed(
                message.idempotency_key,
                error=safe_error,
            )
            session.commit()
        raise
    finally:
        close = getattr(email_sender, "close", None)
        if callable(close):
            close()

    with factory() as session:
        PredictionPipelineRepository(session).mark_email_sent(
            message.idempotency_key,
            provider_message_id=delivery.message_id,
            sent_at=delivery.sent_at.astimezone(UTC),
        )
        session.commit()
    return delivery

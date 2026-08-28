#!/usr/bin/env python3
"""Prove, against production, that nothing a prediction used arrived after 08:30.

Every result in the research report rests on one claim: the features behind a
prediction for day D were all available before D's cutoff. The schema carries
CHECK constraints that enforce it at write time, which is the right place, but
a constraint proves what the database refused to store -- not that the rows
that *are* stored say what they should. A constraint added after a backfill,
or a column left NULL because nothing filled it, both pass silently.

So this reads the stored rows and asks the question directly, three times, once
per timestamp:

    available_timestamp   when the value became public
    first_observed_at     when this system first saw it
    retrieved_at          when it was pulled into storage

All three must be at or before the feature set's cutoff. "Published yesterday
at 18:00 but only fetched this morning at 09:00" passes the first and fails the
third, and it is exactly the case a single timestamp hides.

A prediction with no recorded inputs is not a pass either. It is not a
violation -- nothing was found -- but nothing was checked, and those are
different answers that must not be reported as one. Feature history here is
evicted after the two most recent runs, because a morning writes about 133 MB
into a 512 MB database, so older sessions have no provenance left to read. The
audit says so instead of counting them clean.

One violation makes the whole out-of-sample record INVALID. There is no
threshold and no proportion -- a leak is not diluted by the predictions that
did not have one.

Usage:
    python -m scripts.audit_leakage
    python -m scripts.audit_leakage --json artifacts/oos/leakage_audit.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import Engine

from data.env import EnvironmentSettings
from database.connection import create_database_engine

JST = ZoneInfo("Asia/Tokyo")

# The declared boundary. It is written on every feature set as ``cutoff_at``;
# this is here so a set stamped with some other time is caught rather than
# quietly audited against its own convenient cutoff.
DECLARED_CUTOFF = time(8, 30)

COLUMNS = ("available_timestamp", "first_observed_at", "retrieved_at")


@dataclass(frozen=True, slots=True)
class Violation:
    """One stored row that was not available when it was used."""

    prediction_date: str
    ticker: str
    feature: str
    input_role: str
    column: str
    value: str
    cutoff: str
    late_by_minutes: float


@dataclass
class Audit:
    """What the stored provenance says, and whether it holds."""

    predictions: int = 0
    feature_sets: int = 0
    feature_values: int = 0
    feature_inputs: int = 0
    dates: tuple[str, ...] = ()
    violations: list[Violation] = field(default_factory=list)
    unchecked_predictions: list[str] = field(default_factory=list)
    wrong_cutoff: list[str] = field(default_factory=list)
    missing_maxima: list[str] = field(default_factory=list)
    verified_dates: tuple[str, ...] = ()

    @property
    def verified_predictions(self) -> int:
        return self.predictions - len(self.unchecked_predictions)

    @property
    def verdict(self) -> str:
        """Three answers, because "found nothing" and "checked nothing" differ.

        Collapsing them was the first version of this script: it called a
        retention boundary INVALID, which reads as a leak having been found.
        """

        if self.violations or self.wrong_cutoff or self.missing_maxima:
            return "INVALID"
        if self.unchecked_predictions:
            return "VERIFIED WITHIN RETENTION"
        return "NO LEAKAGE DETECTED"


def _minutes_late(value: datetime, cutoff: datetime) -> float:
    return (value - cutoff).total_seconds() / 60.0


def _violations(engine: Engine) -> list[Violation]:
    """Every input row whose own timestamps postdate the cutoff it was used for.

    One query per column rather than an OR across all three: the column that
    failed is the diagnosis. A late ``retrieved_at`` beside a timely
    ``available_timestamp`` is a fetch that ran after the deadline; all three
    late together is a series that simply had not been published yet.
    """

    found: list[Violation] = []
    for column in COLUMNS:
        statement = text(
            f"""
            select fs.prediction_date, fs.ticker, fv.feature_name,
                   fi.input_role, fi.{column} as stamp, fs.cutoff_at
            from feature_inputs fi
            join feature_values fv
              on fv.feature_value_id = fi.feature_value_id
            join feature_sets fs
              on fs.feature_set_id = fv.feature_set_id
            where fi.{column} > fs.cutoff_at
            order by fs.prediction_date, fs.ticker
            """
        )
        with engine.connect() as connection:
            for row in connection.execute(statement):
                found.append(
                    Violation(
                        prediction_date=str(row.prediction_date),
                        ticker=str(row.ticker),
                        feature=str(row.feature_name),
                        input_role=str(row.input_role),
                        column=column,
                        value=row.stamp.isoformat(),
                        cutoff=row.cutoff_at.isoformat(),
                        late_by_minutes=round(
                            _minutes_late(row.stamp, row.cutoff_at), 1
                        ),
                    )
                )
    return found


def _unchecked(engine: Engine) -> list[str]:
    """Predictions with no input provenance behind them.

    Not a pass. A prediction whose inputs were never recorded is one this audit
    cannot speak for, and a report that counts it as clean is claiming coverage
    it does not have.
    """

    statement = text(
        """
        select ps.prediction_date, p.ticker
        from predictions p
        join prediction_sets ps on ps.prediction_set_id = p.prediction_set_id
        where ps.status = 'READY'
          and not exists (
            select 1
            from feature_sets fs
            join feature_values fv on fv.feature_set_id = fs.feature_set_id
            join feature_inputs fi on fi.feature_value_id = fv.feature_value_id
            where fs.prediction_date = ps.prediction_date
              and fs.ticker = p.ticker
          )
        order by ps.prediction_date, p.ticker
        """
    )
    with engine.connect() as connection:
        return [
            f"{row.prediction_date} {row.ticker}"
            for row in connection.execute(statement)
        ]


def _wrong_cutoff(engine: Engine) -> list[str]:
    """Feature sets stamped with a cutoff other than the declared one."""

    statement = text(
        """
        select feature_set_id, prediction_date, ticker, cutoff_at
        from feature_sets
        order by prediction_date, ticker
        """
    )
    wrong: list[str] = []
    with engine.connect() as connection:
        for row in connection.execute(statement):
            local = row.cutoff_at.astimezone(JST)
            if local.time() != DECLARED_CUTOFF or local.date() != row.prediction_date:
                wrong.append(
                    f"{row.prediction_date} {row.ticker} cutoff={local.isoformat()}"
                )
    return wrong


def _missing_maxima(engine: Engine) -> list[str]:
    """Sets that have inputs but left the summary maxima NULL.

    The constraints on ``feature_sets`` are written as "NULL or <= cutoff", so a
    NULL satisfies every one of them while recording nothing. A set with rows
    beneath it and no maxima above it has not been checked by the schema at all.
    """

    statement = text(
        """
        select fs.prediction_date, fs.ticker
        from feature_sets fs
        where (fs.max_available_timestamp is null
               or fs.max_first_observed_at is null
               or fs.max_retrieved_at is null)
          and exists (
            select 1
            from feature_values fv
            join feature_inputs fi on fi.feature_value_id = fv.feature_value_id
            where fv.feature_set_id = fs.feature_set_id
          )
        order by fs.prediction_date, fs.ticker
        """
    )
    with engine.connect() as connection:
        return [
            f"{row.prediction_date} {row.ticker}"
            for row in connection.execute(statement)
        ]


def _verified_dates(engine: Engine) -> tuple[str, ...]:
    """The sessions that still have input provenance to read."""

    statement = text(
        """
        select distinct fs.prediction_date
        from feature_sets fs
        join feature_values fv on fv.feature_set_id = fs.feature_set_id
        join feature_inputs fi on fi.feature_value_id = fv.feature_value_id
        order by 1
        """
    )
    with engine.connect() as connection:
        return tuple(str(row[0]) for row in connection.execute(statement))


def audit(engine: Engine) -> Audit:
    with engine.connect() as connection:
        counts = connection.execute(
            text(
                """
                select
                  (select count(*) from predictions p
                     join prediction_sets ps
                       on ps.prediction_set_id = p.prediction_set_id
                    where ps.status = 'READY') as predictions,
                  (select count(*) from feature_sets) as feature_sets,
                  (select count(*) from feature_values) as feature_values,
                  (select count(*) from feature_inputs) as feature_inputs
                """
            )
        ).one()
        dates = [
            str(row[0])
            for row in connection.execute(
                text("select distinct prediction_date from feature_sets order by 1")
            )
        ]

    return Audit(
        predictions=int(counts.predictions),
        feature_sets=int(counts.feature_sets),
        feature_values=int(counts.feature_values),
        feature_inputs=int(counts.feature_inputs),
        dates=tuple(dates),
        violations=_violations(engine),
        unchecked_predictions=_unchecked(engine),
        wrong_cutoff=_wrong_cutoff(engine),
        missing_maxima=_missing_maxima(engine),
        verified_dates=_verified_dates(engine),
    )


def _lines(result: Audit) -> list[str]:
    span = f"{result.dates[0]} 〜 {result.dates[-1]}" if result.dates else "(対象なし)"
    verified = (
        f"{result.verified_dates[0]} 〜 {result.verified_dates[-1]}"
        if result.verified_dates
        else "(なし)"
    )
    out = [
        f"LEAKAGE VERIFICATION: {result.verdict}",
        "",
        f"  公開済み期間       {span}（{len(result.dates)}営業日）",
        f"  公開済み予測       {result.predictions}件",
        f"  検証できた予測      {result.verified_predictions}件"
        f"（{verified} / {len(result.verified_dates)}営業日）",
        f"  検証できない予測    {len(result.unchecked_predictions)}件"
        "（保持期間外・provenanceが削除済み）",
        f"  入力行            {result.feature_inputs}件",
        "",
        "  検査項目                                       違反件数",
        "  -----------------------------------------  ----------",
    ]
    for column in COLUMNS:
        late = sum(1 for v in result.violations if v.column == column)
        pad = max(0, 41 - len(column) - 10)
        out.append(f"  {column} > cutoff{' ' * pad}{late:>10}")
    out += [
        f"  cutoff が08:30 JSTでないセット{' ' * 13}{len(result.wrong_cutoff):>10}",
        f"  maxima が NULL のセット{' ' * 20}{len(result.missing_maxima):>10}",
        "",
    ]
    if result.violations:
        out.append("  最初の10件:")
        for violation in result.violations[:10]:
            out.append(
                f"    {violation.prediction_date} {violation.ticker}"
                f" {violation.feature} [{violation.input_role}]"
                f" {violation.column}={violation.value}"
                f" ({violation.late_by_minutes:+.1f}分)"
            )
        out.append("")
    if result.verdict == "INVALID":
        out.append(
            "  1件でも違反があれば全期間のOOS結果は INVALID です。"
            "割合では薄まりません。"
        )
    elif result.verdict == "VERIFIED WITHIN RETENTION":
        out += [
            f"  保持されている{len(result.verified_dates)}営業日ぶんは、"
            "3つのtimestampすべてが全入力行でcutoff以前でした。",
            "  それ以前の営業日は、1営業日あたり約133MBのfeature履歴が"
            "512MB上限のために削除済みで、",
            "  「違反なし」ではなく「検証不能」です。両者を同じ扱いにはしません。",
        ]
    else:
        out.append("  3つのtimestampすべてが全入力行でcutoff以前でした。")
    return out


def _payload(result: Audit) -> dict[str, Any]:
    data = asdict(result)
    data["verdict"] = result.verdict
    data["generated_at"] = datetime.now(JST).isoformat()
    return data


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    engine = create_database_engine(EnvironmentSettings().reporting_database_url())
    try:
        result = audit(engine)
    finally:
        engine.dispose()

    print("\n".join(_lines(result)))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(_payload(result), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    return 0 if result.verdict != "INVALID" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["Audit", "Violation", "audit", "main"]

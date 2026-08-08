#!/usr/bin/env python3
"""Report configured free-data coverage and optionally verify Yahoo symbols."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from data.config import load_app_config
from data.env import EnvironmentSettings
from data.fetch import build_fetch_plan, verify_yahoo
from data.providers.yahoo import YahooFinanceProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument(
        "--network",
        action="store_true",
        help="also perform best-effort Yahoo metadata requests",
    )
    return parser


def build_report(config_dir: Path, *, network: bool) -> dict[str, Any]:
    """Build a machine-readable feasibility report without requiring secrets."""

    config = load_app_config(config_dir)
    plan = build_fetch_plan(config)
    report: dict[str, Any] = {
        "status": "CONFIG_VALID",
        "free_stack": {
            "primary": config.settings.provider.primary,
            "treasury": config.settings.provider.treasury,
            "fallback": list(config.settings.provider.fallback),
        },
        "coverage": {
            "stocks": len(plan.stocks),
            "historical_indicators": len(plan.eod),
            "snapshot_indicators": len(plan.snapshots),
            "treasury_tenors": len(plan.treasury_symbols),
        },
        "unresolved_required": list(plan.unresolved_required),
        "network_check": "SKIPPED",
    }
    if network:
        environment = EnvironmentSettings()
        provider_settings = config.settings.provider
        provider = YahooFinanceProvider(
            timeout_seconds=environment.http_timeout_seconds,
            max_retries=provider_settings.max_retries,
            backoff_seconds=provider_settings.backoff_initial_seconds,
        )
        try:
            report["network_check"] = verify_yahoo(config, provider)
        finally:
            provider.close()
    return report


def main() -> int:
    args = _parser().parse_args()
    report = build_report(args.config_dir, network=args.network)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

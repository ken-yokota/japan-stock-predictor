#!/usr/bin/env python3
"""Send one operator status report in the approved layout.

Progress mails used to be assembled by throwaway scripts, which is how they
drifted into unreadable prose. This builds them from the same tables the
prediction and result mails use, so "readable" is a property of the code path
rather than of whoever wrote the mail.

The report is described as JSON so a caller -- a watcher, a scheduled job, or
a person -- only has to say what the rows are:

    {
      "title": "進捗報告",
      "lede": "工程 2/4 実行中",
      "sections": [
        {"title": "いま進めているタスク",
         "headers": [["工程", "center"], ["内容", "left"], ["想定", "right"]],
         "rows": [[{"text": "2/4", "align": "center"},
                   {"text": "本番でのフル実行"},
                   {"text": "30分", "align": "right"}]],
         "note": "経過22分。想定を超えています。"}
      ],
      "footer": "操作は不要です。"
    }

Usage:  python -m scripts.send_status_report report.json [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from data.env import EnvironmentSettings
from notifications.contracts import RenderedEmail
from notifications.report_layout import BAND, badge, cell, page, row, section, table
from notifications.senders import GmailSmtpSender


def _cell(spec: Any) -> str:
    """One cell, from either a bare string or a spec object."""

    if not isinstance(spec, dict):
        return cell(str(spec))
    if spec.get("badge"):
        return cell(
            badge(str(spec["badge"]), str(spec.get("tone", "wait"))),
            align=str(spec.get("align", "center")),
        )
    return cell(
        str(spec.get("text", "")),
        align=str(spec.get("align", "left")),
        muted=bool(spec.get("muted", False)),
        nowrap=bool(spec.get("nowrap", True)),
    )


def _section(spec: dict[str, Any]) -> str:
    rows = [
        row(
            [_cell(item) for item in line],
            "#fff" if index % 2 == 0 else BAND,
        )
        for index, line in enumerate(spec.get("rows", []))
    ]
    body = ""
    if rows:
        headers = [
            (str(name), str(align)) for name, align in spec.get("headers", [])
        ]
        body = table(headers, rows, min_width=int(spec.get("min_width", 460)))
    return section(str(spec.get("title", "")), body, str(spec.get("note", "")))


def render_status_report(report: dict[str, Any]) -> str:
    """Render the report's HTML without sending anything."""

    return page(
        str(report.get("title", "進捗報告")),
        str(report.get("lede", "")),
        [_section(item) for item in report.get("sections", [])],
        str(report.get("footer", "操作は不要です。")),
    )


def plain_text(report: dict[str, Any]) -> str:
    """The text/plain alternative, so no client is left with an empty body."""

    lines = [str(report.get("title", "")), str(report.get("lede", "")), ""]
    for item in report.get("sections", []):
        lines.append(f"■ {item.get('title', '')}")
        for line in item.get("rows", []):
            values = [
                str(part.get("text", part.get("badge", "")))
                if isinstance(part, dict)
                else str(part)
                for part in line
            ]
            lines.append("  " + " / ".join(values))
        if item.get("note"):
            lines.append(f"  {item['note']}")
        lines.append("")
    lines.append(str(report.get("footer", "")))
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="JSON description of the report")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    subject = str(report.get("subject") or report.get("title") or "進捗報告")
    html_body = render_status_report(report)
    text_body = plain_text(report)
    if args.dry_run:
        print(json.dumps({"status": "DRY_RUN", "subject": subject}, ensure_ascii=False))
        return 0

    environment = EnvironmentSettings()
    sender_address, recipient = environment.require_email_addresses()
    if not environment.smtp_username or environment.smtp_password is None:
        raise RuntimeError("SMTP credentials are not configured")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:12]
    sender = GmailSmtpSender(
        username=environment.smtp_username,
        app_password=environment.smtp_password.get_secret_value(),
        host=environment.smtp_host,
        port=environment.smtp_port,
    )
    delivery = sender.send(
        RenderedEmail(
            subject=subject,
            text=text_body,
            html=html_body,
            sender=sender_address,
            recipient=recipient,
            idempotency_key=f"status/{stamp}/{digest}",
        )
    )
    print(
        json.dumps(
            {"status": "SENT", "provider": delivery.provider, "subject": subject},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

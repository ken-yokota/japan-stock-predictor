"""Mail a Markdown file as-is.

The operator asked for the same report in Markdown rather than as HTML tables.
Markdown survives being pasted into another tool, and the tables stay readable
in a monospace font on a phone, which the coloured HTML version does not
guarantee once it is forwarded.

The Markdown is the message: it goes in the text part unchanged, and the HTML
part is the same text inside <pre>. Nothing is reformatted, so what arrives is
what the file says.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path


def build(markdown: str, subject: str) -> tuple[str, str, str]:
    body = markdown.strip()
    escaped = html.escape(body)
    html_body = (
        '<html><body style="margin:0;padding:16px;background:#ffffff;">'
        '<pre style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
        "font-size:12px;line-height:1.5;color:#111;white-space:pre-wrap;"
        'word-break:break-word;margin:0;">'
        f"{escaped}"
        "</pre></body></html>"
    )
    return subject, body, html_body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)

    text = arguments.markdown.read_text(encoding="utf-8")
    subject, body, html_body = build(text, arguments.subject)
    if arguments.dry_run:
        print(body[:2000])
        return 0

    from scripts.send_status_report import send_rendered

    try:
        provider = send_rendered(subject, body, html_body)
    except Exception as error:
        print(f"send failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"status": "SENT", "provider": provider, "chars": len(body)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

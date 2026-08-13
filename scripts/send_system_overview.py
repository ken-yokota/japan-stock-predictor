"""Mail one description of the whole system: how it works, and what is wrong.

The operator has asked what this thing actually is several times, and the
answer has been scattered across chat. This puts it in one message that can be
re-read on a phone without scrolling back through a conversation.

It is written rather than measured on purpose. The live numbers already have
their own mail; this is the shape of the system and the state of its known
problems, which changes slowly and is worth stating deliberately. Anything
here that stops being true should be edited here.
"""

from __future__ import annotations

import argparse
import json
import sys

from notifications.report_layout import badge, cell, page, row, section, table

BAND = "#f6f7f9"

# The layout defines: now, done, wait, warn, fail.
_TONES = {"ok": "done", "warn": "warn", "bad": "fail", "wait": "wait"}


def _rows(items: list[list[str]]) -> str:
    return "".join(
        row(
            [cell(text, nowrap=False) for text in item],
            "#fff" if index % 2 == 0 else BAND,
        )
        for index, item in enumerate(items)
    )


def _status_rows(items: list[tuple[str, str, str, str]]) -> str:
    return "".join(
        row(
            [
                cell(name, nowrap=False),
                cell(badge(state, _TONES.get(tone, "wait")), align="center"),
                cell(detail, nowrap=False),
            ],
            "#fff" if index % 2 == 0 else BAND,
        )
        for index, (name, state, tone, detail) in enumerate(items)
    )


def build() -> tuple[str, str, str]:
    """Return subject, text body and HTML body."""

    subject = "【システム構成】日本株予測システムの現状と課題"

    flow = [
        ["08:05", "外部スケジューラがGitHubを起動", "cron-job.org"],
        ["08:06", "海外市場データを取得", "Yahoo Finance / 米国債"],
        ["08:10", "特徴量を作り、22銘柄ぶん学習して予測", "Ridge + ロジスティック回帰"],
        ["08:15", "予測を保存し、公開", "Neon PostgreSQL"],
        ["08:16", "買い候補をメール送信", "Gmail SMTP"],
        ["08:17", "ダッシュボードと公開JSONを更新", "Streamlit Cloud / GitHub"],
        ["09:10", "予測・メール・DBの状態を検証", "異常ならアラートメール"],
        ["15:45", "引けの実績を取り込み、損益を確定", "Close update"],
        ["17:00", "その日のまとめをメール送信", "Daily summary"],
    ]

    parts = [
        ["データ取得", "Yahoo Finance（株価22銘柄・為替・指数・商品）、米国財務省（金利）"],
        ["予測", "直近120営業日で学習。Ridge回帰が値幅を、ロジスティック回帰が上昇確率を出す"],
        ["対象", "海運3・石油4・自動車5・金融5・商社5 の22銘柄"],
        ["判断の締切", "毎朝08:30 JST。これ以降に判明した情報は一切使わない"],
        ["保存先", "Neon（PostgreSQL・無料枠512MB）"],
        ["表示", "Streamlit Cloud のダッシュボード＋GitHub上の公開JSON"],
        ["通知", "Gmail経由。朝の予測、日次まとめ、異常アラート"],
        ["実行基盤", "GitHub Actions（無料）＋外部スケジューラ"],
    ]

    healthy = [
        ("先読み防止", "OK", "ok", "08:30以降の情報は構造的に使えない。テスト9本で固定"),
        ("朝の起動時刻", "OK", "ok", "外部トリガーで08:05に固定。実証済み"),
        ("DB容量", "OK", "ok", "209MB / 512MB。1回の書き込みを99%削減済み"),
        ("二重実行", "OK", "ok", "予測・メールとも冪等。同日に二重で作られない"),
        ("ダッシュボード", "OK", "ok", "復旧済み。DBが落ちても公開JSONは別経路で読める"),
    ]

    problems = [
        ("予測が当たるか", "未検証", "bad", "BUY通算8件・的中3件。判断にはサンプルが2桁足りない"),
        ("FX3系列の欠損", "未解決", "warn", "USD/JPY等が毎朝取得失敗。全22銘柄の必須指標"),
        ("朝以外の遅延", "未対応", "warn", "引け更新+78分、日次+76分、監視+152分"),
        ("必須指標の扱い", "検討中", "wait", "欠けても予測は出る。記録は明朝から取れる"),
        ("トークン期限", "要対応", "wait", "2027-08-12に失効。切れると静かに遅延へ戻る"),
    ]

    blocks = [
        section(
            "毎朝の流れ",
            table(
                [("時刻", "center"), ("処理", "left"), ("使うもの", "left")],
                _rows(flow),
                min_width=460,
            ),
            "時刻はJST。すべての処理は二重に走っても安全な作りで、"
            "外部起動とGitHubの定時実行が互いの保険になっています。",
        ),
        section(
            "構成",
            table(
                [("領域", "left"), ("中身", "left")], _rows(parts), min_width=440
            ),
        ),
        section(
            "動いているもの",
            table(
                [("項目", "left"), ("状態", "center"), ("根拠", "left")],
                _status_rows(healthy),
                min_width=480,
            ),
        ),
        section(
            "課題",
            table(
                [("項目", "left"), ("状態", "center"), ("内容", "left")],
                _status_rows(problems),
                min_width=480,
            ),
            "最大の未解決事項は一番上です。配管はほぼ整いましたが、"
            "予測そのものが有効かどうかは、まだ判断できるだけの件数がありません。",
        ),
    ]

    lede = "22銘柄・毎朝08:30締切・BUY通算8件（的中3件）・DB 209MB/512MB"
    footer = (
        "研究用の情報提供です。投資助言ではありません。"
        "この内容は scripts/send_system_overview.py にあり、"
        "変わったらそこを直してください。"
    )
    html_body = page(subject, lede, blocks, footer)

    lines = [subject, "", lede, "", "■ 毎朝の流れ"]
    lines += [f"  {a:6} {b}（{c}）" for a, b, c in flow]
    lines += ["", "■ 構成"]
    lines += [f"  {a}: {b}" for a, b in parts]
    lines += ["", "■ 動いているもの"]
    lines += [f"  [{s}] {n} — {d}" for n, s, _t, d in healthy]
    lines += ["", "■ 課題"]
    lines += [f"  [{s}] {n} — {d}" for n, s, _t, d in problems]
    lines += ["", footer]
    return subject, "\n".join(lines), html_body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print without sending")
    arguments = parser.parse_args(argv)

    subject, text_body, html_body = build()
    if arguments.dry_run:
        print(text_body)
        return 0

    from scripts.send_status_report import send_rendered

    try:
        provider = send_rendered(subject, text_body, html_body)
    except Exception as error:
        print(f"send failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "SENT", "provider": provider}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

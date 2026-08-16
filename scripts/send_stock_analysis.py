"""Mail the per-ticker coefficient analysis, one section per stock.

Reads the JSON `analyze_stock_coefficients` writes, so the numbers in the mail
are the ones that were measured rather than ones re-derived here.

Every stock shows importance and stability side by side on purpose. Ridge never
zeroes a coefficient, so importance alone says only that a weight is large - and
a large weight whose sign flips between sessions is absorbing noise, which looks
better than a small stable one until the two are printed together.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dashboard.catalog import stock_label
from notifications.report_layout import badge, cell, page, row, section, table

BAND = "#f6f7f9"


def _rows(items: list[list[str]]) -> str:
    return "".join(
        row(
            [cell(text, nowrap=False) for text in item],
            "#fff" if index % 2 == 0 else BAND,
        )
        for index, item in enumerate(items)
    )


def _stability_badge(value: float) -> str:
    if value >= 0.80:
        return badge(f"{value:.2f}", "done")
    if value >= 0.65:
        return badge(f"{value:.2f}", "warn")
    return badge(f"{value:.2f}", "fail")


def _stock_section(ticker: str, item: dict[str, Any]) -> str:
    head = [
        [
            "予測成績",
            f"方向的中 {item['direction_accuracy']:.3f}　"
            f"MAE {item['mae_pp']:.3f}pt　"
            f"BUY {item['buy_hits']}/{item['buys']}",
        ],
        [
            "係数の安定",
            f"平均符号安定 {item['mean_sign_stability']:.3f}　"
            f"不安定 {item['unstable_features']}/{item['features']} 本",
        ],
    ]
    features = [
        [
            f"{f['feature'][:38]}",
            f"{f['mean_coef']:+.5f}",
            f"±{f['std_coef']:.5f}",
            f"{f['sign_stability']:.2f}",
        ]
        for f in item["top_features"][:6]
    ]
    indicators = ", ".join(
        f"{i['indicator']}" for i in item["top_indicators"][:6]
    )
    return section(
        f"{ticker} {stock_label(ticker)}",
        table([("項目", "left"), ("値", "left")], _rows(head), min_width=440)
        + table(
            [
                ("特徴量", "left"),
                ("平均係数", "left"),
                ("ばらつき", "left"),
                ("符号安定", "left"),
            ],
            _rows(features),
            min_width=470,
        ),
        f"依存の大きい指標: {indicators}",
    )


def build(payload: dict[str, Any]) -> tuple[str, str, str]:
    tickers: dict[str, Any] = payload["tickers"]
    window = payload["window"]
    subject = f"【銘柄別分析】各銘柄の指標係数と安定性（{len(tickers)}銘柄）"

    ordered = sorted(tickers.items())
    stabilities = [v["mean_sign_stability"] for v in tickers.values()]
    accuracies = [
        v["direction_accuracy"] for v in tickers.values() if v["direction_accuracy"]
    ]

    method = [
        ["目的", "各銘柄のモデルが実際にどの指標へ重みを置いたかを係数から測る"],
        ["重要度", "標準化後の係数の絶対値。Ridgeは係数をゼロにしないため順位で測る"],
        ["符号安定", "max(正の回数, 負の回数) / 全セッション。1.00=常に同じ向き"],
        ["窓", f"{window[0]} 〜 {window[1]}、学習は各日の直前120営業日"],
        ["注意", "係数は相関の重み。因果ではない"],
    ]

    overview = [
        ["銘柄数", str(len(tickers))],
        ["特徴量セット", f"{payload['feature_set']}（27指標）"],
        [
            "平均符号安定",
            f"{sum(stabilities)/len(stabilities):.3f}"
            f"（最低 {min(stabilities):.3f} / 最高 {max(stabilities):.3f}）",
        ],
        [
            "方向的中",
            f"平均 {sum(accuracies)/len(accuracies):.3f}"
            f"（最低 {min(accuracies):.3f} / 最高 {max(accuracies):.3f}）",
        ],
    ]

    blocks = [
        section(
            "分析の目的と手法",
            table([("項目", "left"), ("内容", "left")], _rows(method), min_width=460),
            "重要度と安定性を必ず並べています。符号が日ごとに反転する係数は"
            "関係を記述しておらず、ノイズを吸収しています。大きくて不安定な"
            "係数は、小さくて安定した係数より悪いにもかかわらず、重要度だけを"
            "見ると優秀に見えます。",
        ),
        section(
            "全体",
            table([("項目", "left"), ("値", "left")], _rows(overview), min_width=440),
        ),
    ]
    blocks += [_stock_section(t, v) for t, v in ordered]

    lede = (
        f"{len(tickers)}銘柄・{window[0]}〜{window[1]}・"
        f"平均符号安定 {sum(stabilities)/len(stabilities):.3f}"
    )
    footer = (
        "研究用の情報提供です。投資助言ではありません。"
        "生成元: scripts/analyze_stock_coefficients.py"
    )
    html_body = page(subject, lede, blocks, footer)

    lines = [subject, "", lede, "", "■ 分析の目的と手法"]
    lines += [f"  {a}: {b}" for a, b in method]
    lines += ["", "■ 全体"] + [f"  {a}: {b}" for a, b in overview]
    for ticker, item in ordered:
        lines += ["", f"■ {ticker} {stock_label(ticker)}"]
        lines.append(
            f"  方向的中 {item['direction_accuracy']:.3f}  "
            f"MAE {item['mae_pp']:.3f}pt  BUY {item['buy_hits']}/{item['buys']}  "
            f"符号安定 {item['mean_sign_stability']:.3f}  "
            f"不安定 {item['unstable_features']}/{item['features']}"
        )
        for f in item["top_features"][:6]:
            lines.append(
                f"    {f['feature'][:38]:38} {f['mean_coef']:+.5f} "
                f"±{f['std_coef']:.5f}  安定 {f['sign_stability']:.2f}"
            )
    lines += ["", footer]
    return subject, "\n".join(lines), html_body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)

    payload = json.loads(arguments.payload.read_text(encoding="utf-8"))
    subject, text_body, html_body = build(payload)
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

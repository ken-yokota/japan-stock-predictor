"""Render the per-ticker analysis as Markdown, with every feature kept.

The mail is the summary; this is the record behind it. Nothing is truncated,
because the question it exists to answer - "what is this stock's model actually
leaning on" - cannot be answered from a top-five list.

Importance and stability always appear together. Ridge never zeroes a
coefficient, so a large weight only means large; a large weight whose sign
flips between sessions is absorbing noise and looks better than a small stable
one until both are on the page.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dashboard.catalog import stock_label

SECTOR = {
    "1605": "石油", "5019": "石油", "5020": "石油", "5021": "石油",
    "9101": "海運", "9104": "海運", "9107": "海運",
    "7201": "自動車", "7203": "自動車", "7267": "自動車",
    "7269": "自動車", "7270": "自動車",
    "8306": "金融", "8316": "金融", "8411": "金融",
    "8604": "金融", "8766": "金融",
    "8001": "商社", "8002": "商社", "8031": "商社",
    "8053": "商社", "8058": "商社",
}


def render(payload: dict[str, Any]) -> str:
    tickers: dict[str, Any] = payload["tickers"]
    window = payload["window"]
    out: list[str] = []
    add = out.append

    add("# 銘柄別 指標係数の詳細分析")
    add("")
    add(f"- 特徴量セット: `{payload['feature_set']}`")
    add(f"- 窓: {window[0]} 〜 {window[1]}")
    add(f"- 学習: 各予測日の直前 {payload['training_window']} 営業日")
    add(f"- 銘柄数: {len(tickers)}")
    add("")
    add("## 分析の目的")
    add("")
    add("設定ファイルは「何を渡したか」しか語りません。**モデルが何に重みを")
    add("置いたか**は係数を見ないと分かりません。37指標 x 22銘柄 x 63セッションの")
    add("係数を実測し、銘柄ごとに3つを問います。")
    add("")
    add("| 問い | 測り方 |")
    add("|---|---|")
    add("| 何に依存しているか | 標準化後の係数の絶対値による順位。"
        "Ridgeは係数をゼロにしないため、順位で測るしかない |")
    add("| それは安定しているか | `max(正の回数, 負の回数) / 全セッション`。"
        "1.00=常に同じ向き、0.50=コイン投げ |")
    add("| それは当たっているか | 銘柄別の方向的中・MAE・BUY成績 |")
    add("")
    add("特徴量はパイプライン内で標準化されるため、**単位の違う指標同士の")
    add("重みが比較可能**です。これが順位に意味を持たせている唯一の根拠です。")
    add("")
    add("**係数は相関の重みであり、因果ではありません。**")
    add("")

    accuracies = [v["direction_accuracy"] for v in tickers.values()]
    stabilities = [v["mean_sign_stability"] for v in tickers.values()]
    buys = sum(v["buys"] for v in tickers.values())
    hits = sum(v["buy_hits"] for v in tickers.values())
    add("## 全体")
    add("")
    add("| 項目 | 値 |")
    add("|---|---|")
    add(f"| 方向的中 | 平均 {sum(accuracies)/len(accuracies):.4f}"
        f"（{min(accuracies):.3f} 〜 {max(accuracies):.3f}）|")
    add(f"| 符号安定 | 平均 {sum(stabilities)/len(stabilities):.4f}"
        f"（{min(stabilities):.3f} 〜 {max(stabilities):.3f}）|")
    add(f"| BUY | {hits}/{buys}（{hits/buys:.3f}）|" if buys else "| BUY | 0 |")
    add("")

    for ticker, item in sorted(tickers.items()):
        sector = SECTOR.get(ticker, "—")
        add(f"## {ticker} {stock_label(ticker)}（{sector}）")
        add("")
        add("| 項目 | 値 |")
        add("|---|---|")
        add(f"| 方向的中 | {item['direction_accuracy']:.4f} |")
        add(f"| MAE | {item['mae_pp']:.4f} %ポイント |")
        add(f"| BUY | {item['buy_hits']}/{item['buys']} |")
        add(f"| 平均符号安定 | {item['mean_sign_stability']:.4f} |")
        add(f"| 不安定な特徴量 | {item['unstable_features']}/{item['features']} 本"
            "（符号安定 0.70 未満）|")
        add(f"| セッション数 | {item['sessions']} |")
        add("")
        add("### 指標別の依存度（全指標）")
        add("")
        add("| 順位 | 指標 | 平均 \\|係数\\| |")
        add("|---:|---|---:|")
        for rank, ind in enumerate(item["top_indicators"], 1):
            add(f"| {rank} | {ind['indicator']} | {ind['mean_abs']:.6f} |")
        add("")
        add("### 特徴量別（全特徴量）")
        add("")
        add("| 順位 | 特徴量 | 指標 | 平均係数 | 標準偏差 | 平均順位 | 符号安定 |")
        add("|---:|---|---|---:|---:|---:|---:|")
        for rank, f in enumerate(item["top_features"], 1):
            add(
                f"| {rank} | `{f['feature']}` | {f['indicator']} | "
                f"{f['mean_coef']:+.6f} | {f['std_coef']:.6f} | "
                f"{f['mean_rank']:.1f} | {f['sign_stability']:.2f} |"
            )
        add("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    payload = json.loads(arguments.payload.read_text(encoding="utf-8"))
    arguments.output.write_text(render(payload), encoding="utf-8")
    print(f"written: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Mail the prediction logic as it is implemented, for evaluation.

Written from the code and `config/*.yaml`, not from design notes: the operator
asked for what the system actually does so they can judge it. Anything here
that stops being true should be changed in `docs/PREDICTION_LOGIC.md` and in
this file together.
"""

from __future__ import annotations

import argparse
import json
import sys

from notifications.report_layout import badge, cell, page, row, section, table

BAND = "#f6f7f9"
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
    subject = "【予測ロジック】実装から読み取った仕様"

    target = [
        ["目的変数", "intraday_return = 終値 / 始値 - 1（当日の寄り引け）"],
        ["締切", "08:30 JST 固定。予測日から決まり、実行開始時刻と無関係"],
        ["対象", "海運・石油・自動車・金融・商社の22銘柄"],
        ["持ち越し", "なし。寄りで買い、同日の大引けで売る"],
    ]

    features = [
        ["自銘柄の価格", "11本。全て1営業日ラグ（目的変数そのものを含むため）"],
        ["海外指標", "37系列 → 約185特徴量。名前は indicator_id__ で始まる"],
        ["比率", "185特徴量に対し学習窓120営業日。予測子が観測数に迫る"],
    ]

    training = [
        ["学習窓", "直近120営業日。毎朝すべて学習し直す（保存モデルなし）"],
        ["単位", "銘柄ごとに独立したモデル"],
        ["前処理", "中央値補完 → 標準化 → 回帰。全てパイプライン内"],
        ["漏洩防止", "補完・標準化は各フォールドの学習部分のみで fit"],
        ["交差検証", "TimeSeriesSplit 5分割、gap=0"],
        ["欠損許容", "1行あたり20%超で不採用"],
        ["乱数", "seed=42、deterministic"],
    ]

    models = [
        ["Ridge", "予測リターン", "alpha ∈ {0.01, 0.1, 1, 10, 100}"],
        ["ロジスティック", "上昇確率", "C ∈ {0.01, 0.1, 1, 10}"],
    ]

    rule = [
        ["予測リターン", "0.30% 超（strict）"],
        ["上昇確率", "60% 以上"],
        ["条件", "両方を同時に満たす場合のみ。片方では買わない"],
        ["資金", "1銘柄 100万円、100株単位"],
        ["コスト", "手数料5bps + スリッページ5bps を片側ずつ（往復20bps）"],
    ]

    measured = [
        ["Rank IC（日次断面）", "+0.1119", "p=0.001。断面の順位付けは有意"],
        ["方向的中", "0.5570", "検出下限は約3.1pp"],
        ["MAE", "1.2564pt", "順位指標と一致しないことがある"],
    ]

    weak = [
        ("FX欠損", "調査中", "bad", "必須3系列が毎朝欠損。提供元にデータはある"),
        ("欠損時の抑止", "未実装", "bad", "必須指標が欠けてもBUYを出す"),
        ("予測子の多さ", "既知", "warn", "Ridgeのalphaが毎回グリッド最大"),
        ("冗長指標", "既知", "warn", "金利の一次従属、現物と先物の併存"),
        ("標本", "不足", "warn", "63セッションでは取引成績を検定できない"),
    ]

    blocks = [
        section(
            "何を予測しているか",
            table([("項目", "left"), ("内容", "left")], _rows(target), min_width=440),
            "前日終値からのギャップではありません。この区別が候補指標の"
            "大半を無効にします — 「米株が上げたから日本株も上げる」は真ですが、"
            "その情報は買値である寄り付きに既に入っています。",
        ),
        section(
            "特徴量",
            table([("種別", "left"), ("内容", "left")], _rows(features), min_width=440),
        ),
        section(
            "学習",
            table([("項目", "left"), ("内容", "left")], _rows(training), min_width=440),
            "先に直近120営業日を取り、その後に目的変数が欠けた行を捨てます。"
            "順序が逆だと、欠損が学習窓を過去へ静かに延ばします。",
        ),
        section(
            "2つのモデル",
            table(
                [("モデル", "left"), ("出力", "left"), ("探索範囲", "left")],
                _rows(models),
                min_width=460,
            ),
        ),
        section(
            "買いの判定",
            table([("項目", "left"), ("値", "left")], _rows(rule), min_width=440),
            "往復コストは0.20%で、閾値0.30%との差が実質的な期待幅です。",
        ),
        section(
            "実測（63セッション・1,386予測）",
            table(
                [("指標", "left"), ("値", "left"), ("注記", "left")],
                _rows(measured),
                min_width=460,
            ),
            "Rank ICが区別できない4つの特徴量セットで、BUY成績は +0.44 〜 "
            "-0.01 に開きました。順位指標だけでは採否を決められません。",
        ),
        section(
            "既知の弱点",
            table(
                [("項目", "left"), ("状態", "center"), ("内容", "left")],
                _status_rows(weak),
                min_width=480,
            ),
        ),
    ]

    lede = "22銘柄・寄り引け予測・08:30締切・Rank IC +0.112 (p=0.001)"
    footer = (
        "研究用の情報提供です。投資助言ではありません。"
        "全文は docs/PREDICTION_LOGIC.md にあります。"
    )
    html_body = page(subject, lede, blocks, footer)

    lines = [subject, "", lede, "", "■ 何を予測しているか"]
    lines += [f"  {a}: {b}" for a, b in target]
    lines += ["", "■ 特徴量"] + [f"  {a}: {b}" for a, b in features]
    lines += ["", "■ 学習"] + [f"  {a}: {b}" for a, b in training]
    lines += ["", "■ モデル"] + [f"  {a} → {b} / {c}" for a, b, c in models]
    lines += ["", "■ 買いの判定"] + [f"  {a}: {b}" for a, b in rule]
    lines += ["", "■ 実測"] + [f"  {a}: {b} — {c}" for a, b, c in measured]
    lines += ["", "■ 既知の弱点"] + [f"  [{s}] {n} — {d}" for n, s, _t, d in weak]
    lines += ["", footer]
    return subject, "\n".join(lines), html_body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
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

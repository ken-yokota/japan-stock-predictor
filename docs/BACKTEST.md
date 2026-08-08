# Walk-Forwardバックテスト

更新日: 2026-08-08

## 方法

`backtest.walk_forward_validate`は銘柄ごとに日付順へ並べ、予測位置`t`に対して`[t-120, t)`だけで再学習し、直後の1行だけを予測する。成功行は常に`training_end < prediction_date`である。ticker/date重複は順序が曖昧になるため拒否する。

```text
sessions 1..120 -> predict 121
sessions 2..121 -> predict 122
sessions 3..122 -> predict 123
...
```

各stepでimputer、scaler、Ridge/Logistic、hyperparameter selectionを再fitする。予測後に初めてその日のactual returnを評価へ加える。これがOut-of-Sample（OOS）の基本単位である。

## 出力

成功/不十分status、ticker、prediction date、training start/end/count、predicted return、probability、actual return、選択alpha/C、回帰/分類係数をDataFrameで返す。`python -m scripts.run_walk_forward`はDBのraw履歴からestimated-PIT frameを構築し、銘柄別CSVと`summary.json`を既定の`artifacts/backtest/`へ出力する。結果をlive `metric_snapshots`へ自動保存しないため、live evaluationと混在しない。

```bash
python -m scripts.run_walk_forward \
  --from-date 2023-08-01 --to-date 2026-08-07 \
  --ticker 9101 --output-dir artifacts/backtest
```

## 売買シミュレーション

BUY ruleは`predicted_return > 0.3%`かつ`probability_up >= 60%`。BUY日のみ当日Openで買い、大引けCloseで全量売る。overnight保有はしない。初期default:

- 1銘柄あたり資金: 1,000,000円
- board lot: 100株
- commission: 5 bps / side
- slippage: 5 bps / side
- fixed fee: 0円

```text
buy_fill  = raw_open  × (1 + slippage_rate)
sell_fill = raw_close × (1 - slippage_rate)
shares    = floor(available capital / cost of one 100-share lot) × 100
gross P/L = (raw_close - raw_open) × shares
commission = (buy_notional + sell_notional) × commission_rate
slippage = ((buy_fill-raw_open) + (raw_close-sell_fill)) × shares
net P/L = gross P/L - commission - slippage
return_on_capital = net P/L / 1,000,000
```

0株ならtrade不成立。計算はpaper-onlyで、broker orderは送らない。default costは安全側の研究仮定であり、実際の証券会社見積ではない。

## Leakage検査

- score日以後のraw行を入力へ追加しても当日scoreが変わらないこと。
- `training_end < prediction_date`を全成功行でassertすること。
- scaler/imputerがfold外をfitしないこと。
- 当日Open/Closeはoutcomeとexecutionにだけ使うこと。
- historical revisionを後から過去cutoffへ遡及させないこと。

## 解釈

metricはOOSだけで計算する。銘柄、期間、signal閾値、コスト、欠損policyを変更した結果の中から良いものだけ選ぶとselection biasが生じる。取引件数20未満はLOW_SAMPLEとしてReadabilityへ線形penaltyを掛ける。無料Providerのhistorical availability推定、delisting/survivorship、corporate action、寄り付き約定可能性、税金、流動性、limit up/downは完全には再現していない。

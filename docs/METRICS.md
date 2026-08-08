# MetricsとScore

更新日: 2026-08-08

すべてOOS prediction/paper tradeだけで計算する。nullやsample statusを0と混同しない。close pipelineは日次のlive OOS metric snapshotを更新する。過去全期間のwalk-forward batchとは評価windowを分ける。

## 取引指標

`p_i`を各BUY tradeのnet profit、`r_i`をcapital比returnとする。

| 指標 | 式 / 定義 |
|---|---|
| Number of Trades | 有限な`p_i`の件数 |
| Wins / Losses | `p_i > 0` / `p_i < 0`。break-evenはどちらにも入れない |
| Win Rate | Wins / Trades |
| Gross Profit | `sum(p_i > 0)` |
| Gross Loss | `abs(sum(p_i < 0))` |
| Net Profit | `sum(p_i)` |
| Average / Largest Win | 正profitの平均 / 最大 |
| Average / Largest Loss | 負profitの平均 / 最小（負符号保持） |
| Payoff Ratio | Average Win / abs(Average Loss) |
| Profit Factor | Gross Profit / Gross Loss |
| Expectancy | `win_rate × avg_win - loss_rate × abs(avg_loss)`。break-evenを母数に含む |
| Sharpe | `mean(r) / sample_sd(r) × sqrt(252)`、risk-free=0 |
| Sortino | `mean(r) / sqrt(mean(min(r,0)^2)) × sqrt(252)` |
| Maximum Drawdown | `cumprod(1+r)`のpeak-to-trough最大下落率（正の値で保存） |

分母0の場合、分子が正ならPF/Payoff/Sortinoは`inf`となり得る。永続化・UIではsample statusと併記し、有限値前提のDB列へ無加工で保存しない。

## 予測指標

| 指標 | 定義 |
|---|---|
| Pearson Correlation | predicted returnとactual returnの線形相関 |
| Spearman Correlation | tieを平均rank化した順位相関 |
| Direction Accuracy | `(predicted > 0) == (actual > 0)`の比率 |

2点未満、定数系列、finite pairなしなど未定義の場合、純粋計算関数は0を返す。

## Readability（0〜100）

componentを0〜100へ正規化する。PFは`min(PF/2, 1)×100`、win/direction/stabilityは0〜1を100倍、負または未定義correlationは0点。

```text
unpenalized =
  PF score                    × 35%
  + Win Rate score            × 25%
  + Prediction Correlation    × 20%
  + Direction Accuracy        × 10%
  + Coefficient Stability     × 10%

sample_penalty = min(number_of_trades / 20, 1)
Readability = unpenalized × sample_penalty
```

20 trade未満はLOW_SAMPLE。in-sample metricを渡すと関数は拒否する。

## Coefficient Stability

直近20 rolling fitsについてfeature別に、平均、母標準偏差、majority sign比率を求める。

```text
relative_variation = coefficient_sd / mean(abs(coefficient))
feature_stability = sign_consistency / (1 + relative_variation)
aggregate = observed feature stabilityの単純平均
```

全て0の係数はsign consistency 1。観測なしは0。

## Confidence（0〜100）

これは勝率推定ではなく表示用の相対的なdata/model confidence indicatorである。

```text
conviction = abs(probability_up - 0.5) × 2
magnitude  = min(abs(predicted_return) / 0.01, 1)
raw = conviction×35% + magnitude×20% + Readability/100×25% + coverage×20%
Confidence = raw × 100
```

回帰方向と分類方向が不一致なら0.5倍する。各componentは0〜1にclipする。予測利益を保証しない。

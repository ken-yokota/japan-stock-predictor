# 実装上のDefaultと前提

更新日: 2026-08-08

| 項目 | Default | 状態 / 理由 |
|---|---|---|
| 市場価格Primary | Yahoo Finance / yfinance | confirmed。完全無料。ただし非公式 |
| Treasury | U.S. Treasury XML | confirmed。2Y/10Y/30Y公式値 |
| EODHD | Free key任意、5 calls/run | confirmed。大量Primaryにはしない |
| Timezone | Asia/Tokyo | confirmed |
| Prediction cutoff | 08:30 JST固定 | confirmed。実行遅延で動かさない |
| Target | 当日raw Close / raw Open - 1 | confirmed |
| Training window | 直前120 JPX営業日 | confirmed |
| Minimum complete targets | 120 | confirmed。足りなければINSUFFICIENT_DATA |
| Feature warmup | 20 sessions | confirmed |
| Max feature missing ratio | 20% | confirmed。imputerはtraining fold内だけ |
| Regression / classification | Ridge / Logistic | confirmed |
| ElasticNet / OLS / Lasso | 実装済みだが診断用の比較専用 | confirmed。`compare_regression_candidates`が同一TimeSeriesSplit foldで順位付けする。比較結果でproduction modelを自動昇格させない |
| CV | TimeSeriesSplit 5、gap 0 | confirmed |
| Random seed | 42 | confirmed |
| BUY | return > 0.3%、probability >= 60% | confirmed |
| Morning reference price | 前日終値 | confirmed。実Openではない |
| Actual Open取得後のPredicted Close | `actual_open × (1 + predicted_return)`を派生表示 | confirmed。朝の保存済みreference priceを上書きしない。08:30公開レコードはPIT証跡なので改変しない。Openが無い間は`PENDING`で、前日終値ベースの数字をOpen基準として見せない |
| Capital | 1銘柄1,000,000円 | confirmed paper assumption |
| Board lot | 100株 | confirmed initial JP cash-equity default。銘柄別例外は未実装 |
| Commission | 5 bps / side | confirmed conservative research default。broker quoteではない |
| Slippage | 5 bps / side | confirmed conservative research default。流動性modelではない |
| Overnight | なし | confirmed。当日Open買い・Close売り |
| Email primary | Gmail SMTP STARTTLS port 587 | confirmed free path。App Password使用 |
| Email optional | Resend | optional。API keyがある場合だけ |
| Dashboard | DB read-only | confirmed。UIから取得・学習しない |
| Backtest画面の再計算 | 保存済みOOS予測へ売買条件だけを再適用 | confirmed。閾値/資金/コスト/Top Nは画面で変更可。モデルと学習期間は予測自体が変わるため`walk-forward` batch再実行が必要 |
| Production DB | PostgreSQL / Neon Free想定 | User Action。SQLiteはtest用途 |
| Iron Ore | 欠損のまま除外 | unresolved。偽のproxyや未来値で補完しない |

## Historical PITの前提

Yahoo/Treasuryのbackfillは「当時アプリが何時に観測したか」を完全には返さない。historical training rowは市場終了時刻、Provider/source rule、保守的lagによるestimated availabilityを利用する。一方、毎朝のoperational score rowは実際の`first_observed_at`と`retrieved_at`をcutoff以前に要求する。

この差により、historical OOSは厳格なevent-time replayより楽観的になり得る。日次運用開始後に蓄積したfirst-observedデータだけのsubperiodも別に評価する。

## 変更時の規則

threshold、cost、銘柄、indicator、feature、model gridを変更したらversion/config hashを変え、旧結果と混ぜずにwalk-forwardを再計算する。data qualityを改善するProvider差し替えでも、過去結果の比較可能性を保つためdata/versionを記録する。

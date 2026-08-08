# 日次運用

更新日: 2026-08-08

## 目標タイムライン

| JST | 自動処理 | 利用者の確認 |
|---|---|---|
| 08:20 | GitHub Actionsが無料データ取得、08:30 cutoffで特徴量・予測を作成 | ActionsのMorning predictionとSystem Statusを確認 |
| 08:45 | READY prediction setからメール送信。08:50/08:55はidempotent retry | スマホでBUY候補、warning、cutoff、dashboard linkを確認 |
| 09:00 | 自動発注しない | 最終的な売買判断は利用者自身。必要ならDashboardを開く |
| 15:45 | actual Open/Close、paper P/L、OOS metricsを更新。15:55/16:10 retry | FINAL/PENDING、raw timestampを確認 |

大引け後のpipeline/scriptは実装済みだが、実Neon、actual publication lag、連続営業日のscheduled実行は未検証である。初回は必ずmanual workflowで確認する。

## 初回準備

```bash
python -m scripts.bootstrap_history --from-date 2023-08-01 --to-date 2026-08-07
alembic upgrade head
```

朝処理をlocal DBへ手動実行する入口は`python -m scripts.run_morning_prediction`を使う。

## 朝の確認順

1. GitHub Actions runがsuccessまたは意図したpartialか。
2. Dashboard System StatusでPrediction Setが`READY`か。
3. `Generated`が08:30 cutoffより後でも、入力のavailable/observed/retrievedがcutoff以前か。
4. `Fallback`, `Stale/Missing`, `Unverified/Delayed`件数。
5. 各予測のstatus、feature coverage、warning。
6. BUY候補が0なら、それは正常な結果かdata不足かを区別する。
7. メールが複数届いていないか、`email_logs`が1 recipient/templateにつき1件か。

## Dashboardの見方

- Today: 最新publication、BUY順位、予測return、上昇確率、confidence、interval、warning。
- Stock Detail: 銘柄別の予測/actual履歴、metric、paper P/L。
- Factor Analysis: 最新標準化係数。係数の大きさを因果関係と解釈しない。
- Sector Analysis: 最新予測の単純平均。銘柄数や欠損差に注意。
- Backtest: 保存済みOOS metricとpaper trade。取引件数とcost statusを先に見る。画面上で再学習はしない。
- System Status: DBに保存された最後のrun/provider選択/ingestion。live pingではない。

## 状態の判断

| 状態 | 意味 | 対応 |
|---|---|---|
| `READY` / `SUCCESS` | 所定の計算・保存が完了 | warningとqualityも確認 |
| `PARTIAL` | 一部source/tickerが不足 | failed symbolとfeature coverageを確認。未来値で補完しない |
| `INSUFFICIENT_DATA` | 120 target、feature、missing gate不足 | 履歴bootstrap、Provider、欠損原因を確認 |
| `PENDING` | actual/metric/email等が未確定 | retry時刻まで待ち、後続runを確認 |
| `FAILED` | step失敗 | sanitized error、Actions log、DB run stepを確認 |
| `SKIPPED` | JPX休場日等 | 正常なskipかcalendarを確認 |

## 手動rerun

GitHub Actionsの`workflow_dispatch`で予測日とdry-runを指定する。同じversionの日次predictionは再利用され、メールはDB keyで通常の重複を抑止する。SMTPの結果が曖昧な通信断後に安易にDB行を削除して再送しない。

## 障害対応

- Yahoo障害: stale/missingのまま除外。EODHD FreeはEODだけquota内でfallback可能。
- Treasury障害: 既存値がcutoff/age条件を通らなければ除外。
- DB障害: 予測をメール本文だけで生成しない。DB復旧後にrerun。
- メール障害: Dashboardを正とする。credentialをlogへ出さず、Gmail App PasswordまたはResend設定を確認。
- Actions遅延: cutoffを実行時刻に動かさない。08:30後に取得したsnapshotを朝データとして保存・採用しない。

## 定期保守

週次にProvider selectionと欠損率、月次にOOS metric/sample count/係数安定性を確認する。銘柄・symbol・規約変更はconfig更新とtest後に反映する。raw revisionと監査表は再現性に必要なため、単純な最新値上書きや無計画な削除をしない。

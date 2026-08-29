# 確認・ダッシュボードガイド

更新日: 2026-08-08

このガイドは「どこまで動いているか」「計算結果をどう検証するか」「Dashboardの数字をどう読むか」を実装に沿って説明します。本アプリは研究用で、自動発注、投資助言、収益保証を行いません。

## 1. 最短のローカル確認

```bash
cd /Users/yokotaken/Desktop/japan-stock-predictor
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
docker compose up -d postgres
alembic upgrade head
python -m data.fetch config-check
python -m pytest -q
ruff check .
mypy data database
streamlit run app.py
```

Python 3.14以上が必要です。2026-08-29に全環境を3.14へ揃えました（ローカル・CI 14本・Streamlit Cloud）。`.env`の実値はcommitしません。

## 2. Providerと無料データを確認

秘密不要の構成検査:

```bash
python -m scripts.phase0_data_feasibility
python -m data.fetch config-check
```

Yahooへのbest-effort接続:

```bash
python -m scripts.phase0_data_feasibility --network
python -m data.fetch verify-yahoo
```

EODHD Free keyを設定した場合だけ:

```bash
python -m data.fetch verify-eodhd
python -m data.fetch compare-eod \
  --from-date 2026-08-01 --to-date 2026-08-07 --max-series 5
```

見る点は、22銘柄、historical/snapshot/Treasury対象数、未解決指標、symbol validation、stale/missing、fallbackです。`CONFIG_VALID`や`OK`は設定が妥当という意味で、予測精度や08:30時点の鮮度を保証しません。

Yahoo/yfinanceは取引所公式APIではありません。無料snapshotはrealtimeを保証せず、取得時刻が08:30以前でもmarket timestampが古ければ除外されます。

## 3. DBとmigrationを確認

```bash
alembic current
alembic check
docker compose exec postgres \
  psql -U postgres -d japan_stock_predictor
```

主要表はraw/監査の`market_data`, `stock_prices`, `daily_runs`, `provider_attempts`, `provider_selections`、計算の`feature_sets`, `model_runs`, `prediction_sets`, `predictions`、評価の`actual_results`, `simulated_trades`, `metric_snapshots`、送信の`email_logs`です。

最新run:

```sql
SELECT run_type, prediction_date, cutoff_at, status, current_step,
       started_at, finished_at, failed_symbols
FROM daily_runs
ORDER BY started_at DESC
LIMIT 20;
```

最新PredictionSetと銘柄結果:

```sql
SELECT ps.prediction_date, ps.status AS set_status, ps.cutoff_at,
       ps.generated_at, p.ticker, p.status, p.signal, p.rank,
       p.predicted_intraday_return, p.probability_up,
       p.feature_coverage, p.warnings
FROM prediction_sets ps
JOIN predictions p USING (prediction_set_id)
ORDER BY ps.generated_at DESC, p.rank NULLS LAST, p.ticker
LIMIT 100;
```

PIT上限:

```sql
SELECT ticker, status, cutoff_at,
       max_available_timestamp, max_first_observed_at, max_retrieved_at,
       missing_ratio
FROM feature_sets
ORDER BY created_at DESC
LIMIT 50;
```

READY featureで3つの最大時刻が`cutoff_at`より後なら異常です。

Provider採用と失敗理由:

```sql
SELECT canonical_symbol, interval, selected_provider, selection_role,
       data_quality, freshness_status, coverage, cutoff_at
FROM provider_selections
ORDER BY selected_at DESC
LIMIT 100;

SELECT canonical_symbol, registry_key, priority, accepted,
       data_quality, freshness_status, coverage, reason
FROM provider_attempts
ORDER BY attempted_at DESC
LIMIT 200;
```

## 4. 初期履歴と朝予測

```bash
python -m scripts.bootstrap_history \
  --from-date 2023-08-01 --to-date 2026-08-07
python -m scripts.run_morning_prediction --prediction-date 2026-08-10
```

実在するJPX営業日を指定してください。朝pipelineは休場日なら`SKIPPED`、同日同versionが確定済みなら再利用します。無料取得が一部失敗しても、未来値で補完せず、銘柄ごとに`SUCCESS`または`INSUFFICIENT_DATA`として一括公開します。

予測値の確認式:

```text
target = raw_close / raw_open - 1
predicted difference = previous_close × predicted_return
display predicted close = previous_close × (1 + predicted_return)
BUY = predicted_return > 0.003 AND probability_up >= 0.60
```

referenceは朝時点の前日終値です。実際の当日Openとは異なります。

## 5. メール確認

まず外部送信なしでrenderだけを確認します。dry-runは`email_logs`をclaimしません。

```bash
python -m scripts.send_morning_email \
  --prediction-date 2026-08-10 --dry-run
```

GmailではGoogle 2段階認証とApp Passwordを使います。`EMAIL_PROVIDER=gmail_smtp`、host `smtp.gmail.com`、port `587`、username/password/from/toを設定してからdry-runなしで1回だけ実行します。

```sql
SELECT recipient, template_version, subject, status,
       attempt_count, provider_message_id, created_at, sent_at, last_error
FROM email_logs
ORDER BY created_at DESC
LIMIT 20;
```

同じPredictionSet/recipient/templateはDBで重複を抑止します。ただしGmail SMTP自体にはidempotency APIがなく、サーバ受理直後の通信断では配信成否が曖昧です。厳密なexactly-onceは保証できません。Resendは任意でprovider-side idempotency keyを使います。

## 6. 寄り付き・大引け確認

```bash
python -m scripts.update_open --prediction-date 2026-08-10
python -m scripts.run_close_update --prediction-date 2026-08-10
```

`update_open`はYahooの最初のregular-session 1分barを`DELAYED`/非公式として実観測し、Actual Openを`PENDING`として保存します。取得失敗やfuture timestampは欠損のままにし、朝のPredictionを上書きしません。close updateはYahoo当日EODを取り、15:30 close + 20分およびrawの最終観測を確認してからFINAL/CORRECTED actual、paper trade、metric snapshotを保存します。値が未公開ならPENDINGのまま次のretryへ回るのが正常です。

```sql
SELECT p.ticker, ar.result_version, ar.status,
       ar.actual_open, ar.actual_close, ar.actual_intraday_return,
       st.status AS trade_status, st.shares, st.net_profit_jpy,
       st.is_simulated
FROM predictions p
LEFT JOIN actual_results ar USING (prediction_id)
LEFT JOIN simulated_trades st USING (prediction_id)
ORDER BY ar.created_at DESC NULLS LAST
LIMIT 100;
```

`is_simulated`は常にtrueです。commission/slippageは初期defaultで各side 5 bps、1銘柄100万円、100株lotです。

## 7. Dashboardの読み方

### Today

最初にcutoff/generated/statusとalertを読みます。その後BUY順位、予測return、上昇確率、confidence、interval、feature coverage、warningを見ます。BUY 0件は正常な場合もありますが、全銘柄INSUFFICIENT_DATAなら履歴やProviderの問題です。

### Stock Detail

predictionとactualを日付順で比較し、OOS sample count、PF、Expectancy、最大損失、paper P/Lを合わせて見ます。数日だけ当たっていても有効性とは言えません。

### Factor Analysis

標準化係数の絶対値と符号を同一model内で比較します。因果関係ではありません。複数fitのstabilityとProvider品質も必要です。

### Sector Analysis

海運、エネルギー、自動車、金融、商社の単純平均です。銘柄数、missing、training periodが異なる場合は直接比較しません。

### Backtest

保存済みOOS metricとpaper tradeを表示します。画面を開いて再計算はしません。最初に`Sample`, `Trades`, cost pendingを見てからPF/Net Profit/Sharpe/Drawdownを解釈します。

### System Status

DBへ最後に保存されたrun、provider selection、ingestion、raw summaryです。live API pingではありません。Fallback、Stale/Missing、Unverified/Delayedが増えた日は予測値よりdata qualityを優先して確認します。

## 8. GitHub Actionsの確認

CIがgreenであることを先に確認し、scheduled automationを有効化する前に各workflowを`workflow_dispatch`で手動実行します。

```text
08:20  Morning prediction
08:45  Morning email（08:50/08:55 retry）
15:45  Close update（15:55/16:10 retry）
```

repository variable `AUTOMATION_ENABLED=true`を設定するまでscheduled jobを動かさない安全gateを使います。Actions schedulerには遅延があり、時刻ちょうどを保証しません。遅延しても08:30後のsnapshotを朝入力に戻してはいけません。

## 9. 異常時の優先順位

1. secretやURLをlogへ貼らず、Actionsのstep/statusだけ確認。
2. DB `daily_runs`と`run_steps`で失敗範囲を確認。
3. `provider_attempts`でstale、coverage、quota、symbolを確認。
4. `feature_sets`でPIT timestampとmissing ratioを確認。
5. DB障害時はメールだけ生成せず復旧後rerun。
6. SMTP障害時はDashboardを正として参照。

詳しい仕組みは`docs/ARCHITECTURE.md`、計算は`docs/MODELING.md`、`docs/BACKTEST.md`、`docs/METRICS.md`、デプロイは`docs/DEPLOYMENT.md`を参照してください。

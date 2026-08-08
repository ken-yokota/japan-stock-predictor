# Codex Master Prompt — 日本株「寄り付き→大引け」予測システム完全実装

あなたはこのリポジトリの **Lead Engineer / Quant / Data Engineer / ML Engineer / QA / DevOps** として行動してください。
目的は、設計書だけを書くことではありません。**実際にリポジトリを編集し、動くコード・設定・DB・テスト・Streamlitダッシュボード・メール・GitHub Actions・ドキュメントまで実装し、テストが通る状態まで修正すること**です。

## 0. 最重要の進め方

- まず現在のリポジトリ全体を確認する。既存コードがある場合は壊さず、再利用・整理して実装する。空なら必要な構成を新規作成する。
- 質問で止まらない。技術的に合理的なDefaultを選び、`docs/ASSUMPTIONS.md` に記録して先へ進む。
- ユーザーしか決められない事項、外部アカウント登録、課金、API Key、メール送信元ドメイン認証、Secrets入力だけを User Action として残す。
- **秘密情報を要求・表示・ログ出力・Git commitしない。**
- APIキーがなくても、取得層・テスト・モック・ローカルプレビューまで完成させる。
- 「設計案を提示して終了」は禁止。実装→テスト→不具合修正→ドキュメント更新まで進める。
- 変更後は必ずテストを実行し、失敗原因を直す。動かせない外部サービスはモック/統合テスト境界を用意し、未検証事項を明記する。
- 不明なTicker、Endpoint、料金、提供範囲、Market Hours、Delay、データ公開時刻は推測で固定せず、可能なら最新の公式情報で確認する。確認不能なら `UNAVAILABLE` / `FREE_UNVERIFIED` / `INDICATIVE` 等で扱う。
- 怪しいデータで無理に予測を出すより `NO PREDICTION` を選ぶ。

---

# 1. システムの目的

日本株について、**当日08:30 JSTまでに利用可能だった海外情報だけ**を使い、

1. 寄り付き前に当日の日中リターンを予測
2. 09:00以降に実際の寄り付き価格を取得後、予測終値を算出
3. BUY候補をランキング
4. 15:30大引け後に実績を取得
5. 予測精度・損益・勝率・Profit Factor・Expectancy・Readability等を更新
6. Streamlit Dashboardで可視化
7. 毎営業日08:45頃にメール
8. GitHub Actions等で自動運用

まで一気通貫で行う。

売買ルールは **寄り付きで購入→同日15:30大引けで全売却**。持ち越しなし。初期実装では自動発注はしない。

Primary Target:

`intraday_return = Close / Open - 1`

補助:

`price_difference = Close - Open`

モデル学習・モデル比較は `intraday_return` を優先する。

「過去半年」は曖昧な暦日半年ではなく、初期Defaultとして **各予測日の直前120日本営業日** をTraining Windowとする。必ず設定ファイルから変更可能にする。

---

# 2. 対象銘柄

銘柄をコードへ固定しない。`config/stocks.yaml` で管理する。

初期Universe:

## 海運
- 9101
- 9104
- 9107

## 石油・エネルギー
- 1605
- 5020
- 5019
- 5021

## 自動車
- 7203
- 7267
- 7201
- 7269
- 7270

## 金融
- 8306
- 8316
- 8411
- 8604
- 8766

## 比較・総合商社
- 8001
- 8002
- 8031
- 8053
- 8058

Yahoo Finance用Symbol等のProvider固有Symbolは設定層で管理する。例として日本株のYahoo Symbolは通常 `.T` が必要だが、実際に取得できるかPhase 0でvalidateする。

各銘柄設定には最低でも以下を持たせる:

- code
- name
- sector
- enabled
- provider_symbols
- exchange
- market_timezone

---

# 3. データProvider設計

`MarketDataProvider` 抽象層を作り、将来差し替え可能にする。

初期Provider:

- `YahooFinanceProvider`
- `TreasuryProvider`
- `EODHDFreeProvider`

将来拡張用Interface:

- `EODHDPaidProvider`
- `TwelveDataProvider`
- `FuturesProvider`
- `OfficialExchangeProvider`

初期MVPは原則無料。ただし無料にこだわってLook-Ahead Biasや誤データを許容しない。

## Yahoo / yfinance の用途候補
- 日本株OHLC
- 米国株
- ADR
- ETF
- 指数
- FX
- 商品先物
- VIX
- その他Phase 0で検証済みSymbol

Yahooは公式APIではないので、データごとに:
- `provider=YAHOO`
- `is_realtime`
- `is_delayed`
- `data_quality`
- availability根拠

を保存し、Realtimeと決めつけない。

## U.S. Treasury
米国金利は公式データを優先して:
- US2Y
- US10Y
- US30Y
- 10Y-2Y
- 30Y-10Y
- 1日変化
- 3日変化
- 5日変化

を作る。

## EODHD Free
用途:
- Symbol Validation
- Yahooとの照合
- Backup候補
- 将来Paidへの切替準備

Free枠に大量取得を依存させない。

---

# 4. Phase 0 Data Feasibilityを最初に実装・実行

本番モデル実装前に、データ取得可能性を機械的に検証するスクリプトを作る。

調査対象:

1. 初期日本株Yahoo ticker
2. 共通指標
3. 海運
4. 石油
5. 自動車
6. 金融
7. ADR
8. Commodity
9. FX
10. Futures
11. 米国金利取得方法

重点:
- BDI
- BCI
- BPI
- Iron Ore
- Nikkei225 Futures
- S&P500 Futures
- NASDAQ100 Futures
- WTI
- Brent
- USDJPY
- ADR
- 米国金利
- 日本株OHLC

以下の列を持つ `docs/PHASE0_DATA_FEASIBILITY.md` と機械可読なCSV/JSONを生成する:

- Indicator Name
- Symbol
- Provider
- Market
- Timezone
- History
- Intraday
- 08:30 JSTで利用可能か
- Delay
- Availability Rule
- Quality
- Sector
- Priority
- Fallback
- Status
- Notes

Tier:
- Tier1 = 初期必須・無料で比較的安定
- Tier2 = 利用可能だが品質/遅延/履歴に注意
- Tier3 = 有料/不安定/将来候補

**BDI/BCI/BPI/Iron Ore等を無理にスクレイピングしない。**
無料で安定取得できず、08:30時点の履歴再現性も保証できない場合はTier2/Tier3またはUNAVAILABLEにする。

Phase 0結果に基づいて、初期モデルが本当に使用する指標を `config/indicators.yaml` に確定する。

---

# 5. 最重要要件: Look-Ahead Biasをゼロにする

予測日 `t` の `prediction_timestamp` はDefaultで **08:30 JST**。

特徴量へ採用できるデータは必ず:

`feature.available_timestamp <= prediction_timestamp`

を満たすものだけ。

禁止:
- `date == date` だけの単純JOIN
- 08:30以降に確定した当日情報
- 将来日の値
- 全期間でfitしたScaler
- Random K-Fold
- `random train_test_split`
- 後から取得したHistorical EODデータに、根拠なく当日00:00等のavailable_timestampを付けること
- 欠損値を未来側からbackfillすること
- 当日大引け後にしか分からない値を朝予測へ使うこと

実装:
- `available_timestamp` を第一級カラムとして扱う
- Prediction時はas-of selectionを行う
- Timezoneはtimezone-aware
- DST対応
- 米国/中国/香港/日本の祝日・休場・早期終了・前営業日を考慮
- Providerごとの「いつ利用可能になったとみなすか」を明示的なavailability ruleとして実装
- Historical backtestでも、現在取得した値を当時利用可能だったかのように扱わない
- Yahoo等で真のpublish timestampが得られないEOD履歴は、取引所close時刻＋保守的buffer等の再現可能なルールを使い、品質ラベルを落とす
- 根拠が不足する指標はbacktest対象から除外可能にする

必須テスト:
- 未来値が1件でも混入すると失敗
- prediction_timestamp境界
- DST
- 日本/米国祝日
- 異なるtimezone
- market close前後
- historical reconstruction
- as-of join

**Look-Aheadテスト失敗時、そのモデル・Prediction・BacktestをVALIDとして保存/表示してはいけない。**

---

# 6. 保存するMarket Data

最低カラム:

- provider
- symbol
- asset_type
- exchange
- market_timezone
- market_date
- market_timestamp
- available_timestamp
- retrieved_at
- open
- high
- low
- close
- volume
- is_realtime
- is_delayed
- data_quality
- source_metadata
- data_version

Quality例:
- OFFICIAL
- EOD_CONFIRMED
- DELAYED
- FREE_UNVERIFIED
- INDICATIVE
- MISSING
- STALE
- PROVIDER_ERROR

必要ならRaw Snapshot tableも追加する。

---

# 7. 特徴量

120日学習なので特徴量を増やしすぎない。

候補:
- 1日Return
- 2日Return
- 3日Return
- 5日Return
- 20日Return
- Log Return
- 5日Volatility
- 20日Volatility
- Open-Close Return
- High-Low Range
- 20日MA乖離率
- Treasury level / 1d / 3d / 5d changes
- Yield curve spreads

Sector別の候補をconfig化。

## Common
候補:
- SPY
- QQQ
- DIA
- VIX
- USDJPY
- AUDJPY
- Gold
- S&P500 Futures
- NASDAQ100 Futures
- Nikkei225 Futures

Phase 0で08:30利用可能性を確認できたものだけ有効化。

## 海運
候補:
- BDI
- BCI
- BPI
- FXI
- MCHI
- Copper
- Iron Ore
- WTI
- Brent
- USDJPY
- AUDJPY
- SPY
- VIX
- 海外海運株/ETF

## 石油
候補:
- WTI
- Brent
- XLE
- OIH
- Natural Gas
- USDJPY
- SPY
- VIX
- Gold
- China ETF

## 自動車
候補:
- USDJPY
- SPY
- QQQ
- XLI
- FXI
- MCHI
- EWY
- Copper
- WTI
- VIX
- TM ADR Return
- HMC ADR Return

## 金融
候補:
- XLF
- KRE
- US2Y
- US10Y
- US30Y
- 10Y-2Y
- 30Y-10Y
- USDJPY
- SPY
- VIX
- Nikkei Futures
- MUFG ADR Return
- SMFG ADR Return

ADRは直接価格換算よりReturn特徴量として評価する。

Interaction候補:
- WTI × USDJPY
- BDI × USDJPY
- US10Y × USDJPY
- US10Y Change × XLF Return

InteractionはWalk-Forward OOS改善が確認できた場合だけ採用できる構造にする。

特徴量定義とavailability provenanceを `docs/DATA_DICTIONARY.md` に出す。

---

# 8. モデル

Regression:
- Ridge
- ElasticNet

Classification:
- Logistic Regression

Benchmark:
- OLS
- Lasso

Default production candidate:
- Ridge for predicted intraday return
- Logistic Regression for Probability Up

将来拡張:
- RandomForest
- XGBoost
- LightGBM

ただし初期段階で複雑モデルを無理に有効化しない。

## 学習
予測日ごとに:
- 直前120日本営業日だけをtraining windowとして使用
- Scalerはそのtraining windowの中でだけfit
- 特徴量選択/ハイパーパラメータ最適化もtraining window内で完結
- `TimeSeriesSplit` またはNested Time-Series CVのみ
- 未来データをCVへ入れない
- 乱数を使う処理はseed固定

保存:
- model_name
- model_version
- feature_version
- hyperparameters
- coefficients
- intercept
- scaler parameters
- training_start
- training_end
- sample_count
- CV metrics

---

# 9. Walk-Forward OOS

各予測日 `t` について:

1. `t` より前の直近120日本営業日だけ取得
2. train
3. `t` を予測
4. prediction保存
5. 次営業日へ進む

これを反復する。

投資成績として表示できるのは **OOS Predictionのみ**。

In-Sample scoreはモデル診断用に分離し、投資成績として混ぜない。

最低2〜3年分の履歴取得を試みる。ただしProviderで確保できない場合は実取得可能期間を記録し、捏造しない。

---

# 10. BUY判定

初期Default:

- `Predicted Intraday Return > 0.30%`
- AND `Probability Up >= 60%`

閾値はStreamlit UI / configで変更可能にする。

初期投資額:
- 1銘柄 1,000,000円

Shares:
`floor(investment_amount / actual_open)`

損益:
`(actual_close - actual_open) * shares - commission - slippage`

Commission / Slippageはconfig化。

BUY candidateが0件なら:
`本日は条件を満たすBUY候補なし`

をDashboardとEmailで明示する。

---

# 11. 09:00以降のPredicted Close

08:30までは実際のOpenが未確定なので、朝の主予測はReturnとProbability Up。

09:00以降、Actual Open取得後:

`Predicted Close = Actual Open * (1 + Predicted Intraday Return)`

を保存・表示する。

朝メールでActual Openが未取得なら、Predicted Closeを捏造しない。

---

# 12. 評価指標

OOSのみで最低限:

- Trades
- Win Rate
- Gross Profit
- Gross Loss
- Net Profit
- Avg Win
- Avg Loss
- Largest Win
- Largest Loss
- Payoff Ratio
- Profit Factor
- Expectancy
- Sharpe
- Sortino
- Max Drawdown
- Pearson Correlation
- Spearman Correlation
- Direction Accuracy
- MAE
- RMSE

Profit Factor:
`Gross Profit / abs(Gross Loss)`

Gross Loss=0を無条件に「非常に優秀」としない。Sample Sizeと合わせて表示する。

Expectancyについては、プロジェクト定義が曖昧にならないよう:
- 実際のOOS BUYシグナル1回あたり平均Net P/Lを `mean_trade_pnl` として必ず保存
- 率ベースのExpectancy定義をコードと `docs/METRICS.md` に明示
- UI説明は「BUYシグナル1回あたりの過去OOS平均損益」と整合させる
- 数式を黙って変更しない

Sharpe/Sortino/Max Drawdownは日次OOS portfolio/trade seriesから計算し、annualization方法を文書化する。

---

# 13. Readability Score

意味:
「海外指標からその銘柄をどれだけ安定して読み取れるか」

0〜100。

初期Weight:
- Profit Factor 35%
- Win Rate 25%
- Prediction Correlation 20%
- Direction Accuracy 10%
- Coefficient Stability 10%

OOSのみ使用。

20 trades未満は `LOW SAMPLE` を付け、少数サンプルで高得点を出さない。

初期Default normalizationとして、別要件がなければ以下のような**透明でテスト可能な変換**を採用してよい:
- PF component: capped/scaled 0〜1
- Win Rate: 0〜1
- Pearson: -1〜1を0〜1へ変換
- Direction Accuracy: 0〜1
- Stability: 0〜1
- Sample factor: `min(1, trades / 20)`

最終式と各componentの定義は `docs/METRICS.md` とUIへ公開する。
数式はconfig化できる設計が望ましい。

---

# 14. Coefficient Stability

Rolling trainingごとに特徴量係数を保存し:

- Mean Coefficient
- Coefficient SD
- Positive Sign Ratio
- Negative Sign Ratio
- Sign Stability
- Sign Flip Count
- Active Window Count

を計算する。

符号反転が頻繁な特徴量はReadabilityを下げる。

Ridge/ElasticNet等でScalerを使用するため、比較可能なstandardized coefficientを保存する。

---

# 15. Positive / Negative Factors

今日の予測理由として、原則:

`Feature Contribution = coefficient * today's standardized feature value`

を計算する。

Top Positive Factors / Top Negative Factorsを表示する。

注意:
- regression contributionとclassification contributionを混同しない
- defaultはPredicted Returnを説明するRidge contribution
- interceptも必要に応じて表示
- raw value
- standardized value
- coefficient
- contribution
- provider
- data_quality
- available_timestamp

を追跡可能にする。

---

# 16. Confidence

意味:
「今日の予測の信頼度」

Readabilityとは分離する。

候補:
- Probability Up
- Model Agreement
- Historical Accuracy
- Prediction Magnitude
- Feature Data Quality
- Model Stability

初期Weightは合理的Defaultを設定ファイル化し、`docs/ASSUMPTIONS.md` / `docs/METRICS.md` に明記する。

Data Qualityが低い場合はConfidenceを強く下げる。
必須Tier1 feature欠損等ではPrediction自体を `NO PREDICTION` にできるようにする。

---

# 17. DB

MVP:
- SQLite

Production:
- PostgreSQL

ORM:
- SQLAlchemy

`DATABASE_URL` で差し替え可能にする。

最低Tables:
- market_data
- features
- predictions
- actual_results
- model_metrics
- model_coefficients
- trades
- daily_runs
- email_logs
- provider_status

必要なら:
- data_snapshots
- feature_metadata
- model_runs

を追加。

Predictionには最低:
- prediction_date
- stock_code
- predicted_intraday_return
- probability_up
- predicted_close
- prediction_timestamp
- model_version
- feature_version
- data_version
- training_start
- training_end
- status
- confidence
- readability

を保存。

Unique constraint / idempotencyを適切に設定する。

SQLiteはローカルMVP用。
GitHub Actions + 公開Dashboardを継続運用する場合、ephemeral filesystemに依存しないようPostgreSQL等のpersistent DBへ切り替えられる実装・手順を用意する。
SQLite DBを無理にGitへcommitする方式は採用しない。

---

# 18. Streamlit Dashboard

PC / iPhoneブラウザ対応。

Multipage Appを実装。

## Today
表示:
- Prediction date / status
- Last successful run
- TOP BUY ranking
- Stock
- Sector
- Predicted Return
- Probability Up
- Predicted Close（Actual Open取得後のみ）
- Actual Open
- Readability
- Confidence
- Profit Factor
- Win Rate
- Expectancy
- Positive Factors
- Negative Factors
- Data Quality
- BUY / NO BUY / NO PREDICTION
- 08:30 snapshot timestamp

## Stock Detail
- 過去OOS Prediction
- Actual vs Prediction
- Prediction error
- cumulative P/L
- trade history
- PF
- Win Rate
- Expectancy
- coefficient history
- coefficient stability
- feature contribution

## Factor Analysis
- feature importance/coefficient
- stability
- correlation
- sector differences
- missing/data quality

## Sector Analysis
- sector ranking
- OOS metrics
- trade metrics
- factor comparison

## Backtest
UI変更:
- investment amount
- predicted return threshold
- probability threshold
- commission
- slippage
- Top N
- model
- training window

変更した条件でOOS結果を再計算し:
- Total P/L
- PF
- Win Rate
- Expectancy
- Sharpe
- Sortino
- Max Drawdown
- Trades

等を表示。

## System Status
- morning fetch
- prediction
- email
- close update
- DB
- provider status
- missing indicators
- stale data
- last errors
- GitHub Actions想定ステータス
- last successful date

スマホで横に広すぎる表を避け、カード/expandersを適切に使う。

---

# 19. 毎日メール

Resendを使用する送信層を実装。

Default:
- 08:45 JST頃
- 営業日のみ

メール内容:
- Date
- TOP5
- Predicted Return
- Probability Up
- Readability
- Profit Factor
- Win Rate
- Expectancy
- Confidence
- Positive Factors
- Negative Factors
- Data Quality
- Dashboard URL

該当なし:
`本日は条件を満たすBUY候補なし`

メール送信失敗:
- `email_logs` に保存
- retry
- 同一メールを重複送信しない

Idempotency:
- `prediction_date + email_type + recipient` 等でuniqueにする

HTML email + plain text fallbackを用意する。

Secrets:
- `RESEND_API_KEY`
- `EMAIL_TO`
- `EMAIL_FROM`
- `APP_URL`

API Key未設定時はlocal preview / dry-runができるようにする。

---

# 20. 毎営業日Workflow

業務ロジックとして:

## 08:20 JST
1. 日本営業日判定
2. 海外データ取得
3. 08:30以前に利用可能なSnapshot確定
4. Quality検証
5. DB保存
6. 特徴量生成
7. 銘柄別に直前120日本営業日で学習
8. 時系列CV
9. Predicted Return
10. Probability Up
11. BUY ranking
12. Readability / Confidence / Factor contribution
13. DB保存

## 08:45 JST
- Email送信

## 09:00以降
- Actual Open取得
- Predicted Close計算

## 15:45以降
1. Actual Open / Close取得
2. Actual Intraday Return
3. Prediction比較
4. trade P/L
5. win/loss
6. actual_results
7. trades
8. model_metrics
9. readability/stability
10. dashboard用データ更新

時間はconfig化する。

---

# 21. GitHub Actions

作成:
- `.github/workflows/morning_prediction.yml`
- `.github/workflows/morning_email.yml`
- `.github/workflows/close_update.yml`

必要ならmanual workflow_dispatchを全て追加。

注意:
- GitHub Actions scheduled workflowは分単位での厳密実行保証がない
- JSTとUTCの変換を正しく行う
- Workflow内部でも日本営業日判定を必ず行う
- 再実行しても二重登録・二重メールしない
- failure時にログを残す
- timeoutを設定
- retry可能な設計
- DB migration/initを安全に行う
- Secretsをstdoutへ出さない

Dedicated schedulerへ将来移行できるよう、時間依存ロジックをGitHub Actions YAMLへ埋め込みすぎず、Python CLIへ分離する。

---

# 22. 営業日・市場時間

単なる平日判定は禁止。

日本取引所の営業日を扱えるmarket calendarを導入し、以下をtest:
- 土日
- 日本祝日
- 年末年始
- 臨時休場に対応可能な構造
- 米国祝日
- DST
- early close

外部ライブラリのcalendarが不完全な場合のfallback/override設定も用意する。

---

# 23. Error Handling / Data Quality

Provider単発失敗で即全システム停止させない。

実装:
- timeout
- retry
- exponential backoff
- structured logging
- provider health/status
- fallback
- circuit-breaker相当の簡易制御が必要なら導入
- missing policy

Status例:
- SUCCESS
- PARTIAL_SUCCESS
- NO_PREDICTION
- INSUFFICIENT_DATA
- STALE_DATA
- PROVIDER_ERROR
- LOOKAHEAD_VIOLATION
- EMAIL_FAILED

禁止:
- 未来情報で穴埋め
- 不適切なbackfill
- 取得失敗を0で補う
- stale値を無表示で使用

---

# 24. Tests

pytestを中心に、最低限以下を実装:

## Data / Time
- Market Date Alignment
- Timezone
- DST
- Japan holidays
- US holidays
- early close
- available_timestamp boundary
- Look-Ahead Bias
- stale data
- missing data

## Features
- return calculation
- volatility
- MA distance
- yield spread
- interaction
- no future fill

## ML
- Rolling Window = prior 120 business days
- Scaler Leakage禁止
- TimeSeriesSplit
- Walk Forward
- prediction reproducibility
- coefficient storage

## Trading
- BUY threshold
- Shares
- Commission
- Slippage
- P/L
- Win Rate
- PF
- Expectancy
- Sharpe
- Sortino
- Max Drawdown

## Scores
- Readability
- sample penalty
- Confidence
- coefficient stability

## Operations
- duplicate daily job
- duplicate prediction
- duplicate trade
- duplicate email
- provider retry
- API failure
- DB rollback
- email dry-run

可能ならcoverageを計測する。
Look-Ahead関連は特に強いテストを作る。

---

# 25. Repository構成

既存構成に合わせて調整してよいが、最低限この責務分離を満たす。

例:

```text
.
├── app.py
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
├── config/
│   ├── stocks.yaml
│   ├── indicators.yaml
│   ├── model.yaml
│   ├── trading.yaml
│   └── app.yaml
├── src/
│   └── jp_intraday/
│       ├── __init__.py
│       ├── cli.py
│       ├── settings.py
│       ├── logging_config.py
│       ├── calendars/
│       ├── providers/
│       ├── data/
│       ├── db/
│       ├── features/
│       ├── models/
│       ├── backtest/
│       ├── trading/
│       ├── metrics/
│       ├── scoring/
│       ├── email/
│       └── services/
├── pages/
│   ├── 1_Today.py
│   ├── 2_Stock_Detail.py
│   ├── 3_Factor_Analysis.py
│   ├── 4_Sector_Analysis.py
│   ├── 5_Backtest.py
│   └── 6_System_Status.py
├── scripts/
│   ├── phase0_data_feasibility.py
│   ├── bootstrap_history.py
│   ├── run_morning_prediction.py
│   ├── send_morning_email.py
│   ├── update_open.py
│   ├── run_close_update.py
│   └── run_walk_forward.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── docs/
│   ├── FILES.md
│   ├── ARCHITECTURE.md
│   ├── DATA_SOURCES.md
│   ├── DATA_DICTIONARY.md
│   ├── PHASE0_DATA_FEASIBILITY.md
│   ├── MODELING.md
│   ├── BACKTEST.md
│   ├── METRICS.md
│   ├── OPERATIONS.md
│   ├── DEPLOYMENT.md
│   ├── ASSUMPTIONS.md
│   ├── KNOWN_ISSUES.md
│   └── IMPLEMENTATION_REPORT.md
└── .github/
    └── workflows/
        ├── morning_prediction.yml
        ├── morning_email.yml
        └── close_update.yml
```

---

# 26. `docs/FILES.md` を必ず作成

**作成した各種ファイルの説明Markdownを必ず生成する。**

`docs/FILES.md` には、リポジトリ内の主要ファイル/ディレクトリを一覧化して、それぞれ:

- Path
- Purpose
- Main classes/functions
- Input
- Output
- Dependencies
- Called by
- Related tests
- Secrets used（秘密値ではなく環境変数名のみ）
- Notes / operational caution

を説明する。

新しいファイルを追加・削除・役割変更したら `docs/FILES.md` も更新する。

単なるファイル名一覧ではなく、「どこを直せば何が変わるか」が初心者でも分かる内容にする。

---

# 27. その他の必須ドキュメント

## `README.md`
- プロジェクト概要
- 何を予測しているか
- 売買ルール
- Quick Start
- install
- `.env`
- DB init
- data bootstrap
- backtest
- Streamlit起動
- daily commands
- tests
- deployment
- 注意事項

## `docs/ARCHITECTURE.md`
- データ取得→snapshot→features→model→prediction→email→close updateの流れ
- Mermaid diagram
- Provider abstraction
- DB
- idempotency

## `docs/DATA_SOURCES.md`
各指標:
- provider
- symbol
- timezone
- availability
- quality
- fallback
- phase0 result

## `docs/DATA_DICTIONARY.md`
- tables
- columns
- features
- meanings
- units
- timestamps

## `docs/MODELING.md`
- target
- window
- CV
- scaler
- coefficients
- regression/classification
- model selection

## `docs/BACKTEST.md`
- walk-forward
- OOS
- leakage prevention
- trading assumptions
- cost assumptions

## `docs/METRICS.md`
- 全指標の数式
- PF
- Expectancy
- Readability
- Confidence
- coefficient stability

## `docs/OPERATIONS.md`
- 08:20 / 08:45 / 09:00 / 15:45
- rerun
- failure handling
- logs
- recovery

## `docs/DEPLOYMENT.md`
- Streamlit deployment
- GitHub Actions
- PostgreSQL
- Secrets names
- timezone
- scheduler limitation

## `docs/ASSUMPTIONS.md`
- Codexが合理的Defaultとして決めたもの

## `docs/KNOWN_ISSUES.md`
- 無料データの限界
- Yahoo非公式
- futures availability
- historical availability reconstruction
- GitHub Actions timing
- 未検証Provider

## `docs/IMPLEMENTATION_REPORT.md`
実装完了時に:
- Completed
- Files Created
- Files Modified
- Tests
- Test Results
- Phase 0 Results
- Backtest Results
- Known Issues
- Assumptions
- User Action Required
- Next Recommended Improvements

をまとめる。

---

# 28. CLI

人手操作とGitHub Actionsの両方から同じ処理を呼べるCLIを作る。

例:
- `python -m jp_intraday.cli phase0`
- `python -m jp_intraday.cli bootstrap-history`
- `python -m jp_intraday.cli walk-forward`
- `python -m jp_intraday.cli morning`
- `python -m jp_intraday.cli send-email --dry-run`
- `python -m jp_intraday.cli update-open`
- `python -m jp_intraday.cli close`
- `python -m jp_intraday.cli system-status`

実際のpackage構成に合わせて正しいコマンドへ調整する。

---

# 29. Security

`.env.example` に変数名だけ記載。

候補:
- `EODHD_API_KEY`
- `RESEND_API_KEY`
- `EMAIL_TO`
- `EMAIL_FROM`
- `DATABASE_URL`
- `APP_URL`

禁止:
- Secretをコードへ直書き
- SecretをREADMEへ貼る
- Secretをtest fixtureへ保存
- Secretをlog出力
- SecretをGitHub Actions echo
- ChatGPT/Codex応答へSecretをコピーさせる

---

# 30. Dependency / Code Quality

- Python 3.11+を想定し、利用環境に合わせて決定
- dependencyをpin/lock可能な形にする
- type hint
- docstring
- logging
- modularity
- clear error messages
- ruff等のlintを導入可能なら導入
- pytest
- SQLAlchemy
- pandas
- numpy
- scikit-learn
- scipy
- streamlit
- yfinance
- requests/httpx
- pydantic-settings等
- PyYAML
- Resend SDKまたは公式に推奨される送信方法
- market calendar library

ライブラリやAPIの現行仕様は実装時に確認する。

---

# 31. Backtest/UIで必ず区別するもの

- In-Sample
- CV
- Walk-Forward OOS
- Live

を混ぜない。

Dashboardの投資成績はDefaultで **Walk-Forward OOS**。

Live実績が蓄積したら別セクションで表示。

---

# 32. 無料データの扱い

初期は無料で実装するが、Phase 7で以下を測れるようにする:

- Missing Rate
- Stale Rate
- Provider Failure Rate
- Fetch latency
- Historical Coverage
- 08:30 availability confidence
- Prediction improvement
- Realtime need

将来、有料API導入判断ができるレポートを作れる構造にする。

月15,000円以内を基本方針とするが、今は課金しない。

---

# 33. 投資表現

Dashboard/Email/READMEに免責を入れる。

- 利益保証をしない
- 「必ず上がる」「絶対買い」禁止
- Predicted Return
- Probability Up
- OOS performance
- Data Quality
- sample size

を見せる。

自動発注は実装しない。

---

# 34. 実装順

以下の順に実際に進める:

## Phase 0
Data Feasibility

## Phase 1
Data Foundation
- provider
- schema
- DB
- calendars
- availability
- look-ahead tests

## Phase 2
Baseline Prediction
- features
- Ridge
- Logistic
- walk-forward

## Phase 3
Trading Evaluation
- trades
- P/L
- PF
- expectancy
- readability
- stability
- confidence

## Phase 4
Dashboard

## Phase 5
Automation
- Resend
- GitHub Actions
- daily jobs

## Phase 6
Model Improvement
- ElasticNet
- optional interactions
- model comparison
- confidence improvement

## Phase 7 readiness
- live evaluation metrics
- paid data decision support

各Phase完了ごとにコード・test・docsを更新する。ただしユーザーの確認待ちで止まらず、実装可能なところまで連続して進める。

---

# 35. Definition of Done

以下を全て満たすまで「完了」としない:

- [ ] repository code implemented
- [ ] config-driven stocks
- [ ] config-driven indicators
- [ ] Provider abstraction
- [ ] Phase 0 feasibility script/report
- [ ] DB schema
- [ ] SQLite local support
- [ ] PostgreSQL switch support
- [ ] timezone-aware timestamps
- [ ] available_timestamp model
- [ ] look-ahead prevention
- [ ] look-ahead pytest
- [ ] 120-business-day rolling training
- [ ] Ridge
- [ ] Logistic Regression
- [ ] ElasticNet comparison
- [ ] TimeSeriesSplit
- [ ] Walk-Forward OOS
- [ ] regression predictions
- [ ] probability_up
- [ ] BUY thresholds
- [ ] trade P/L
- [ ] PF
- [ ] Win Rate
- [ ] Expectancy
- [ ] Sharpe
- [ ] Sortino
- [ ] Max Drawdown
- [ ] Pearson
- [ ] Spearman
- [ ] Direction Accuracy
- [ ] MAE
- [ ] RMSE
- [ ] Readability
- [ ] Confidence
- [ ] coefficient history
- [ ] coefficient stability
- [ ] positive/negative factors
- [ ] Streamlit Today
- [ ] Stock Detail
- [ ] Factor Analysis
- [ ] Sector Analysis
- [ ] Backtest page
- [ ] System Status
- [ ] responsive/mobile-conscious UI
- [ ] Resend email layer
- [ ] dry-run email
- [ ] GitHub Actions morning prediction
- [ ] GitHub Actions morning email
- [ ] GitHub Actions close update
- [ ] idempotency
- [ ] retry/error handling
- [ ] no secrets in repo
- [ ] README
- [ ] docs/FILES.md
- [ ] architecture docs
- [ ] data source docs
- [ ] model docs
- [ ] metrics docs
- [ ] operations docs
- [ ] deployment docs
- [ ] implementation report
- [ ] tests passing, or unavoidable external integration failures explicitly documented

---

# 36. 最後にCodexが返す内容

実装作業後、チャットでは長い設計説明ではなく以下を簡潔に報告する:

1. Completed
2. Files Created / Modified
3. Tests executed
4. Test result
5. Phase 0 key findings
6. Backtest/OOS result（データが取得できた場合）
7. Known Issues
8. Assumptions
9. User Action Required
10. Exact commands to run locally
11. Exact deployment/Secrets steps that remain

**秘密情報そのものは絶対に表示しない。**

もしAPI KeyやResend認証がないため実送信/本番取得ができない場合でも、
コード、テスト、dry-run、UI、DB、workflows、docsまで完成させた上で、
User Action Requiredへ不足項目だけを残す。

今からこのリポジトリを確認し、上記を実装してください。

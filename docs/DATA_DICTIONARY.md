# データ辞書

更新日: 2026-08-08

## 共通単位

| 項目 | 定義 |
|---|---|
| price | Provider通貨建て。日本株はJPY。raw OHLCとAdjusted Closeを分離 |
| return | 小数。`0.003`は`0.3%`。原則`close / base - 1` |
| yield | Treasuryのpercent point。例`4.25`は4.25% |
| probability / rate / coverage | 0〜1 |
| score | 0〜100 |
| bps | 1 basis point = 0.01%。5 bps = 0.0005 |
| datetime | timezone-aware。DB比較はUTC、画面はJST |

## Raw price共通列

`market_data`と`stock_prices`は次を共有する。

| 列 | 意味 |
|---|---|
| `id` | raw revision row ID |
| `canonical_symbol` | アプリ内部ID。例`usdjpy`、`9101` |
| `symbol` | Provider symbol。例`JPY=X`、`9101.T` |
| `provider` | `yahoo_finance`、`us_treasury`、`eodhd_free`等 |
| `market`, `market_timezone`, `market_date` | 市場分類、IANA timezone、現地session日 |
| `market_timestamp` | 市場イベント/quote時刻 |
| `source_timestamp` | Provider明示時刻。なければnull |
| `available_timestamp` | 利用可能とみなす最早時刻 |
| `first_observed_at`, `retrieved_at`, `last_seen_at` | 初観測、取得完了、最終再確認 |
| `interval` | `1d`、`1m`等 |
| `availability_method` | official schedule、market close lag、observed等の導出方法 |
| `data_quality` | `OFFICIAL`, `EOD_CONFIRMED`, `FREE_UNVERIFIED`, `DELAYED`, `MISSING` |
| `is_realtime`, `is_delayed` | Provider metadata上のリアルタイム/遅延flag。鮮度判定とは別 |
| `open`, `high`, `low`, `close`, `adjusted_close`, `volume`, `currency` | raw値。`close`必須、その他はnullable |
| `raw_hash` | revision同一性を判定するSHA-256 |
| `quality_flags` | proxy、estimated availability等の警告list |

## 監査・選択テーブル

| Table | 主な列と意味 |
|---|---|
| `instrument_mappings` | canonical/provider/provider symbol/exchange/status/verified_at。未対応結果も保存 |
| `daily_runs` | `run_id`, `run_type`, `prediction_date`, `cutoff_at`, status/current_step, version, failed_symbols, sanitized error |
| `ingestion_batches` | providerごとのrequest/success/failure数、insert/reuse数、status |
| `provider_attempts` | series/interval/provider priority、accepted、quality/freshness、expected/actual session、coverage、reason |
| `provider_selections` | run/series/intervalごとのimmutableな採用Provider、PRIMARY/FALLBACK、quality/freshness/cutoff/coverage |
| `run_steps` | retry可能step、attempt番号、RUNNING/SUCCESS/FAILED/SKIPPED、時刻、details |

## Feature・Model・Prediction

| Table | 主な列と意味 |
|---|---|
| `feature_sets` | ticker/date/cutoff、feature version、120-session training range、config/input hash、missing ratio、入力の最大時刻、status |
| `feature_values` | sample date/cutoff、TRAIN/SCORE、FEATURE/TARGET、feature name/value/missing、quality |
| `feature_inputs` | feature valueからraw table/row ID、raw hash、available/observed/retrievedへの正確なlineage |
| `model_runs` | ticker/task/algorithm/version、training range/count、feature set、parameters/intercept、status/idempotency |
| `model_coefficients` | model run、feature名、標準化係数、scaler mean/scale |
| `prediction_sets` | run/date/cutoff、feature/model/strategy version、training range、generated/published、warning、status |
| `predictions` | ticker、feature/model参照、予測return/interval/up probability、reference price/予測close、signal/rank/threshold/confidence/factors/coverage/warnings |

永続化上、`predictions.status`は`SUCCESS`、`INSUFFICIENT_DATA`、`FAILED`である。application内の計算結果が`READY`でも、DBでは成功行を`SUCCESS`として保存する。

## Outcome・paper trade・metric・email

| Table | 主な列と意味 |
|---|---|
| `actual_results` | prediction、version/supersedes、PENDING/FINAL/CORRECTED、actual Open/Close、actual intraday return/difference、raw hash、observed/finalized |
| `simulated_trades` | prediction/actual、NOT_TRIGGERED/PENDING/FINAL/INSUFFICIENT_CONFIG、capital/shares/entry/exit、gross/cost/net、return、strategy version。常に`is_simulated=true` |
| `metric_snapshots` | ticker/as-of/model/strategy/window、sample status/counts、win/PF/expectancy/profit/risk/correlation/direction/readability、input hash |
| `email_logs` | prediction set/recipient/template/subject、PENDING/SENDING/SENT/FAILED、provider message ID、attempt、sanitized error、idempotency key |

## 特徴量

prefixは`stock__`または`<indicator_id>__`となる。

| Feature | 計算 | 単位 |
|---|---|---|
| `return_1d/2d/3d/5d/20d` | `close_t / close_(t-n) - 1` | return |
| `log_return_1d` | `ln(close_t / close_(t-1))` | log return |
| `volatility_5d/20d` | 1日returnのrolling sample標準偏差（ddof=1） | return |
| `open_close_return` | `close / open - 1` | return |
| `high_low_range` | `high / low - 1` | return |
| `ma20_deviation` | `close / MA20(close) - 1` | return |
| Treasury level | tenor yieldそのもの | percent point |
| Treasury change 1/3/5 | `yield_t - yield_(t-n observation)` | percentage point差 |
| `10Y-2Y spread` | `10Y - 2Y` | percentage point |

対象株のscore日特徴量は当日Open/High/Low/Closeを使わず、直前sessionまでで作る。training targetだけが`close / open - 1`であり、そのsession終了後に利用可能となる。

## Prediction表示値

| 値 | 計算 |
|---|---|
| `predicted_intraday_return` | Ridge出力 |
| `probability_up` | Logisticのclass 1確率。training targetが単一classならtraining比率を定数利用 |
| `prediction_interval` | prediction ± `1.96 × training residual sample SD`。厳密な予測区間保証ではない |
| `reference_price` | 朝は前日終値 |
| `predicted_price_difference` | `reference_price × predicted_return` |
| `predicted_close` | `reference_price × (1 + predicted_return)`。朝のreferenceは前日終値。`update_open`はActual Openを別のPENDING outcomeへ保存し、朝のpredictionを上書きしない |
| `BUY` | predicted return `> 0.003` かつ probability `>= 0.60` |

Nullは0を意味しない。欠損・未確定・不十分はnullとstatusで表す。

# 主要ファイルガイド

更新日: 2026-08-08

この一覧は`EXPECTED_REPOSITORY_FILES.md`ではなく、実際のrepositoryを監査して作成した。`API`はmain class/function、`Secrets`は環境変数名だけを記載する。`—`は秘密値を直接使わないことを示す。

## Repository root / config

| Path | Purpose | API | Input | Output | Dependencies | Called by | Tests | Secrets | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `README.md` | 概要、Quick Start、運用入口 | — | repository実装 | 利用手順 | docs | 利用者 | CIでlinkは未検査 | 変数名のみ | 実secretを記載しない |
| `.env.example` | 環境変数template | — | 利用者copy | `.env` | pydantic-settings | local runtime | `test_config.py`, `test_email.py` | `DATABASE_URL`, `EODHD_API_KEY`, `SMTP_*`, `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_TO`, `APP_URL` | 値はdummy/blankのみ |
| `pyproject.toml` | package/dependency/tool設定 | project metadata | pip/pytest/ruff/mypy | build/test設定 | setuptools | CI/local | CI install | — | Python >=3.12 |
| `requirements.txt`, `requirements-dev.txt` | pinned範囲のinstall入口 | — | pip | runtime/dev環境 | PyPI | Actions/local | CI | — | `pyproject.toml`と同期する |
| `config/stocks.yaml` | 22対象株とProvider symbol | stock entries | YAML | `StocksConfig` | `data.config` | fetch/pipeline/dashboard catalog | `test_config.py` | — | Yahoo `.T`; EODHD JPはnull |
| `config/indicators.yaml` | 37指標、source、PIT、sector対応 | indicator/source/group entries | YAML | `IndicatorsConfig` | `data.config` | fetch plan/dataset | `test_config.py`, provider tests | — | proxy/directを区別。Iron Ore未解決 |
| `config/settings.yaml` | provider/schedule/quality/default | settings sections | YAML | `SettingsConfig` | `data.config` | ingestion/pipeline | `test_config.py` | 名前だけ列挙 | EODHD 5 calls/run、08:30 cutoff |
| `config/model.yaml` | window/feature/model/CV grid | model sections | YAML | `ModelConfig` | `data.config` | prediction | `test_config.py`, `test_models.py` | — | productionはRidge/Logistic |
| `config/trading.yaml` | BUY/資金/lot/cost/reference | trading sections | YAML | `TradingConfig` | `data.config` | prediction/close | `test_config.py`, `test_trading.py` | — | 100万円、100株、5+5 bps/side |

## Data Provider / PIT

| Path | Purpose | API | Input | Output | Dependencies | Called by | Tests | Secrets | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `data/config.py` | 4 YAMLをstrict検証しcross-reference | `load_app_config`, config models | config directory | `AppConfig` | Pydantic, PyYAML | 全service/script | `test_config.py` | — | 重複key/unknown field/timezoneも拒否 |
| `data/env.py` | 環境変数をtyped取得 | `EnvironmentSettings`, `require_*` | process env / `.env` | sanitized settings | pydantic-settings | scripts/providers/email | config/email tests | 全runtime secrets | `SecretStr`をlogしない |
| `data/schemas.py` | Provider-neutral market domain | `MarketBar`, `FetchRequest`, `SnapshotRequest`, `SessionOpenRequest`, `DataQuality` | typed values | immutable request/bar | dataclasses/Decimal | providers/router/repository | provider tests | — | timestamp順序を検証 |
| `data/providers/base.py` | Provider interfaceとerror境界 | `MarketDataProvider`, snapshot/catalog protocols | request | bars/health/resolution | ABC/Protocol | concrete providers/router | provider tests | — | business logicはconcrete Yahooへ依存しない |
| `data/providers/yahoo.py` | 無料市場価格Primary | `fetch_eod`, `fetch_snapshot`, `fetch_session_open`, `resolve_symbol` | symbol/date/interval | `MarketBar` | yfinance, pandas | ingestion/fetch/open/close | `test_yahoo_provider.py`, `test_fetch.py` | — | 非公式、`auto_adjust=False`、Openは最初のregular 1分bar |
| `data/providers/treasury.py` | 公式yield XML | `TreasuryProvider.fetch_treasury_yield_bars*` | year/date/tenor | 2Y/10Y/30Y bars | httpx/XML | fetch plan | `test_treasury_provider.py` | — | value公式、historical publish timeは推定 |
| `data/providers/eodhd.py` | 任意Free EOD fallback/validation | `EODHDFreeProvider`, `resolve_symbol`, `fetch_eod` | symbol/date/key | bars/resolution | httpx | fetch CLI/router | `test_eodhd_provider.py` | `EODHD_API_KEY` | live/Treasury禁止、quota 5/run |
| `data/provider_router.py` | 候補品質/PIT/coverage評価と単一Provider選択 | `ProviderRouter.fetch_eod_series/fetch_snapshot` | candidates, cutoff, sessions | selection + attempts | providers/alignment/snapshot | `data.fetch` | `test_provider_router.py` | — | 不足日を別Providerで穴埋めしない |
| `data/availability.py` | cutoffとavailability導出 | `prediction_cutoff`, `eod_availability`, `live_availability` | date/timezone/market close | aware datetime | zoneinfo | providers/router/dataset | lookahead/alignment tests | — | cutoffは08:30 JST固定 |
| `data/alignment.py` | as-of選択とlook-ahead/provider混在拒否 | `latest_available`, `assert_no_lookahead` | bars/cutoff | `AlignedValue` | schemas | router/tests | `test_market_alignment.py`, `test_lookahead.py` | — | revisionの初回観測も検査 |
| `data/snapshot.py` | snapshot age/future/quality gate | `assess_snapshot`, statuses | bar/cutoff/max age | `FreshnessAssessment` | schemas | router | fetch/router tests | — | FRESH以外はscoreから除外 |
| `data/market_calendar.py` | JPX営業日/open/close/window | `is_japan_business_day`, `japan_sessions_before`, `japan_session_open/close` | date/count | sessions/datetime | exchange-calendars | pipeline/dataset | `test_market_calendar.py` | — | 祝日をweekdayだけで判定しない |
| `data/treasury_features.py` | yield spread/changeのPIT派生bar | `build_treasury_features`, `derive_treasury_features` | tenor bars | derived bars | schemas | fetch/dataset | Treasury tests | — | availabilityは最も遅い入力を継承 |
| `data/fetch.py` | config/symbol/compare/fetch CLIとplan execution | `build_fetch_plan`, `execute_fetch_plan`, `main` | config/date/providers | JSON report + DB rows | all data/database | scripts/ingestion | `test_fetch.py` | `DATABASE_URL`, optional `EODHD_API_KEY` | CLIはraw取得、予測はしない |
| `data/logging.py` | secret-safe logging | logging helpers | log level/message | structured logs | stdlib | fetch/runtime | indirect | `LOG_LEVEL` | credentialを含めない |

## Database / migration

| Path | Purpose | API | Input | Output | Dependencies | Called by | Tests | Secrets | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `database/connection.py` | URL正規化、engine/session lifecycle | `create_database_engine`, `create_session_factory`, `session_scope` | DB URL | SQLAlchemy engine/session | SQLAlchemy/psycopg | scripts/dashboard | `test_database.py` | `DATABASE_URL` | production CLIはPostgreSQL、SQLiteはtest |
| `database/models.py` | 全ORM schema | `Base`, raw/audit/feature/model/prediction/actual/trade/metric/email models | ORM values | relational rows | SQLAlchemy | repositories/dashboard | `test_database.py`, `test_prediction_repository.py` | — | constraintでPIT、paper-only、statusを防御 |
| `database/repository.py` | 冪等upsertとstate transition | `MarketDataRepository`, `PredictionPipelineRepository` | domain/ORM values | persisted rows | models/SQLAlchemy | fetch/services/pipeline | database/repository tests | — | email claimはtransaction boundaryに注意 |
| `alembic/versions/0001_phase1_schema.py` | raw/監査初期schema | `upgrade/downgrade` | DB | tables/indexes | Alembic | `alembic upgrade` | migration CI | `DATABASE_URL` | destructive downgradeは本番で安易に実行しない |
| `alembic/versions/0002_free_provider_quality.py` | free provider品質・revision拡張 | `upgrade/downgrade` | 0001 DB | new columns/constraints | Alembic | migration chain | migration CI | `DATABASE_URL` | raw timestamp semanticsを保持 |
| `alembic/versions/0003_prediction_pipeline.py` | feature〜email schema | `upgrade/downgrade` | 0002 DB | 11計算/監査table | Alembic | migration chain | migration/repository tests | `DATABASE_URL` | PredictionSet atomic publication |

## Features / models / evaluation

| Path | Purpose | API | Input | Output | Dependencies | Called by | Tests | Secrets | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `features/domain.py` | PIT feature lineage domain | `FeatureLineage`, `PointInTimeFeatureSet`, `assert_point_in_time_safe` | feature/source timestamps | validated feature set | dataclasses | feature integration/tests | `test_features.py`, `test_lookahead.py` | — | cutoff後sourceを拒否 |
| `features/builder.py` | pure OHLC feature/target計算 | `build_price_features`, `add_intraday_targets` | pandas OHLC | feature DataFrame | pandas/numpy | tests/integrations | `test_features.py` | — | ticker分離、0除算はNaN |
| `models/base.py` | model contracts/config | `ModelTrainingConfig`, `TickerPrediction`, `InsufficientTrainingData` | typed settings | contracts | pandas | training/backtest | `test_models.py` | — | deterministic defaults |
| `models/ridge.py`, `models/classifier.py` | sklearn Pipeline構築 | `build_ridge_pipeline`, `build_logistic_pipeline` | alpha/C | imputer+scaler+model | scikit-learn | training/optimization | `test_models.py` | — | preprocessはfold内fit |
| `models/linear.py` | ElasticNet/Lasso/OLSのpipeline | `build_elastic_net_pipeline`, `build_lasso_pipeline`, `build_ols_pipeline` | alpha/l1_ratio/seed | imputer+scaler+model | scikit-learn | comparison | `test_model_comparison.py` | — | Ridgeと同じpreprocess契約。OLSは診断用baselineでproduction昇格しない |
| `models/comparison.py` | 回帰候補の時系列CV比較 | `compare_regression_candidates`, `RegressionComparison`, `CandidateScore` | training window/候補/grid | 候補別MSE/RMSE/MAEの順位 | models/linear/optimization | 診断・報告 | `test_model_comparison.py` | — | training window内だけで計算。比較結果を投資成績として表示しない。分割不能なsampleは`NOT_EVALUATED` |
| `models/optimization.py` | chronological hyperparameter選択 | `chronological_splitter`, `select_ridge_alpha`, `select_logistic_c` | train data/grid/splits | best alpha/C | TimeSeriesSplit | training/comparison | `test_models.py` | — | future foldでtrainしない |
| `models/training.py` | 銘柄別model fit/predict/係数 | `TickerModelBundle`, `train_ticker_model`, `train_models_by_ticker` | features/target/config | fitted bundle | sklearn/models | prediction/backtest | `test_models.py` | — | 単一classは定数probability |
| `models/prediction.py` | trained modelのthin prediction API | `predict_ticker` | bundle/one row | `TickerPrediction` | models | integrations | `test_models.py` | — | fitはしない |
| `backtest/walk_forward.py` | strict one-step OOS | `walk_forward_validate`, `assert_walk_forward_oos` | long DataFrame/features | OOS DataFrame | models/pandas | walk-forward script | `test_walk_forward.py` | — | `[t-120,t)`で`t`を予測 |
| `backtest/scenario.py` | 保存済みOOS予測へ売買条件を再適用 | `ScenarioConfig`, `evaluate_scenario`, `prepare_scenario_frame` | OOS予測+実績Open/Close、閾値/資金/コスト/Top N | portfolio・銘柄別metric・trade明細 | metrics/trading/pandas | `pages/5_Backtest.py` | `test_scenario.py` | — | modelを再学習しない。model/学習期間の変更は`walk-forward`再実行が必要。試行回数を数えselection biasを警告 |
| `trading/strategy.py` | BUY、100株lot、両側cost paper simulation | `is_buy_signal`, `simulate_intraday_trade`, `simulate_prediction_frame` | prediction/Open/Close/config | `TradeResult`/DataFrame | pandas | close/backtest | `test_trading.py` | — | `held_overnight=False`; broker APIなし |
| `trading/post_open.py` | Actual Open基準のPredicted Close導出 | `project_predicted_close`, `PostOpenProjection` | actual_open、predicted_return | `PostOpenProjection`または`None` | stdlib/Decimal | `dashboard/presenters.py` | `test_post_open.py` | — | 朝の保存値を上書きしない。Openが無ければ`None`を返し、代替値を捏造しない |
| `metrics/performance.py` | PF/Expectancy/risk/correlation/誤差 | `calculate_performance_metrics`, `mean_absolute_error`, `root_mean_squared_error`ほか | OOS P/L/return/predicted/actual | `PerformanceMetrics` | numpy | close/backtest/scenario | `test_metrics.py` | — | undefinedは0/inf規則を確認。MAE/RMSEはreturn単位 |
| `scoring/readability.py` | OOS Readability 0..100 | `score_readability` | metric/stability/trades | components + score | stdlib | close/dashboard/email | `test_scoring.py` | — | 20 trade未満penalty |
| `scoring/confidence.py` | 表示用confidence | `calculate_confidence_score` | prediction/readability/coverage | 0..100 | stdlib | prediction | `test_scoring.py` | — | 勝率ではない |
| `scoring/stability.py` | 直近係数の符号/変動安定性 | `calculate_coefficient_stability`, `aggregate_*` | coefficient history | feature/aggregate score | pandas/numpy | close | `test_scoring.py` | — | default lookback 20 |

## Application services / pipeline / notification

| Path | Purpose | API | Input | Output | Dependencies | Called by | Tests | Secrets | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `services/dataset.py` | DBからPIT dataset構築 | `PointInTimeDatasetBuilder.build/build_backtest_frame`, `ModelDataset`, `BacktestDataset` | session/config/ticker/date | train/currentまたはestimated-PIT frame + lineage | SQLAlchemy/pandas/data | prediction/walk-forward | `test_dataset.py`, lookahead tests | — | operational scoreは実観測時刻も要求 |
| `services/prediction.py` | dataset→fit→予測/BUY/説明 | `PredictionService.compute/predict`, `PredictionResult` | ticker/date/config | computation/model/result | models/scoring/trading | morning | model/dataset tests | — | fail closed `INSUFFICIENT_DATA` |
| `services/persistence.py` | feature/model/prediction artifact永続化 | `persist_feature_set`, `persist_prediction_computation`, `prediction_set_versions` | computation/config/run | ORM rows + hashes | repository/versioning | morning | repository/dataset tests | — | raw lineageをcell単位で固定 |
| `services/ingestion.py` | Yahoo/Treasury/任意EODHDの朝取得 | `ingest_free_morning_data` | factory/config/env/date window | `IngestionOutcome` | data.fetch/providers | morning | fetch/provider tests | `EODHD_API_KEY`, `DATABASE_URL` | failureをsanitizedしrun監査 |
| `services/email.py` | READY setをpayload化しDB冪等送信 | `load_morning_email_payload`, `send_persisted_morning_email` | DB/config/env/date | delivery/none | notifications/repository | email script | `test_email.py` | email/SMTP/Resend/DB variables | DB claim後のexternal side effect |
| `services/versioning.py` | stable hash/version labels | `sha256_json`, `config_hash`, `lineage_manifest_hash` | serializable data | SHA-256/version | hashlib/json | persistence/pipeline | indirect | — | config変更時version policyも確認 |
| `pipeline/morning.py` | 08:20 ingestion→22予測→atomic publish | `MorningPipeline.run`, `MorningPipelineResult` | date/config/env/factory | PredictionSet/run result | all morning services | morning script | dataset/repository tests | market/DB vars | 休場skip、確定済み再利用 |
| `pipeline/open.py` | post-open Actual OpenをPENDING保存 | `OpenPipeline.run` | date/config/env | observed/missing counts | Yahoo 1m/repository | update_open | provider/repository tests | DB | DELAYED first regular bar。strict first-observed。失敗は欠損 |
| `pipeline/close.py` | EOD確定、actual、paper trade、live OOS metric | `ClosePipeline.run`, `ClosePipelineResult` | date/config/env/observed time | finalized/pending/corrected | Yahoo/trading/metrics/scoring/repository | close script | trading/metrics/repository tests | DB | close+20分・last_seen gate |
| `notifications/contracts.py` | provider-neutral email DTO/Protocol | `MorningEmailPayload`, `EmailCandidate`, `EmailSender` | typed values | contracts | dataclasses | templates/senders/service | `test_email.py` | — | DB/provider非依存 |
| `notifications/templates.py` | deterministic HTML/Text | `render_morning_email` | payload/from/to/top N | `RenderedEmail` | html/hashlib | email service | `test_email.py` | address arguments | BUY 0件とwarningを明示 |
| `notifications/senders.py` | Gmail/Resend/dry-run adapter | `GmailSmtpSender`, `ResendSender`, `DryRunSender` | rendered email/credentials | `EmailDelivery` | smtplib/httpx | email service | `test_email.py` | `SMTP_*`, `RESEND_API_KEY` | Gmail厳密exactly-once不可 |
| `notifications/service.py` | render/claim/send/log orchestration | `EmailDispatcher`, `InMemoryEmailLogStore` | payload/sender/store | delivery/none | contracts/templates | tests/general integration | `test_email.py` | injected only | productionは`services.email` DB storeを使用 |

## Dashboard

| Path | Purpose | API | Input | Output | Dependencies | Called by | Tests | Secrets | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `app.py` | Streamlit home | `main` | DB env | overview/navigation | dashboard.ui/Streamlit | `streamlit run` | `test_dashboard.py` | `DATABASE_URL` | read-only |
| `dashboard/query_service.py` | schema-aware read queries | `DashboardQueryService` methods | SQLAlchemy engine | `QueryResult` rows | SQLAlchemy | pages/ui | `test_dashboard.py` | URL injected | raw SQLはselectのみ |
| `dashboard/types.py` | READY/EMPTY/SCHEMA_PENDING/UNAVAILABLE | `QueryResult`, `QueryState` | query rows/error state | typed result | dataclasses | query/ui | `test_dashboard.py` | — | missing migrationをgraceful表示 |
| `dashboard/presenters.py` | format/alerts/aggregate | formatters, `derive_operational_alerts`, `today_table_rows`, `sector_rows` | DB dict rows | safe UI rows/alerts | pandas-free helpers | pages | `test_dashboard.py` | — | secret/errorをtruncate |
| `dashboard/ui.py` | cached serviceと共通widgets | `require_service`, cached queries, render helpers | `DATABASE_URL`/query results | Streamlit components | Streamlit/database | app/pages | `test_dashboard.py` | `DATABASE_URL` | Providerへlive接続しない |
| `dashboard/catalog.py` | ticker/company/sector表示名 | `stock_label`, `sector_label` | ticker | label | static mapping | pages | dashboard tests | — | configとの同期を確認 |
| `pages/1_Today.py` | 今日のpublication/BUY順位 | `main` | latest DB rows | page | dashboard | Streamlit | dashboard tests | DB only | status/warningを先に表示 |
| `pages/2_Stock_Detail.py` | 銘柄別prediction/actual/metric/P&L | `main` | DB history | page/chart/table | dashboard/pandas | Streamlit | dashboard tests | DB only | 少数sampleを警告 |
| `pages/3_Factor_Analysis.py` | 最新係数 | `main` | model coefficients | chart/table | dashboard/pandas | Streamlit | dashboard tests | DB only | 因果ではない |
| `pages/4_Sector_Analysis.py` | 最新予測sector集約 | `main` | predictions/metrics | chart/table | dashboard/pandas | Streamlit | dashboard tests | DB only | 単純平均 |
| `pages/5_Backtest.py` | 保存済みOOS結果 + 条件変更後の再計算 | `main`, `_render_persisted`, `_render_scenario` | metrics/trades/OOS予測 | chart/table/再計算結果 | dashboard/backtest.scenario/pandas | Streamlit | dashboard tests, `test_scenario.py` | DB only | UI上でmodelを再学習しない。閾値/資金/コスト/Top Nのみ変更可 |
| `pages/6_System_Status.py` | run/provider/raw health | `main` | persisted audit | alert/table | dashboard | Streamlit | dashboard tests | DB only | live pingではない |
| `pages/7_Test.py` | 直近期間の検証結果表示 | `main` | `artifacts/week_test/latest.json` | 日別勝率/金額比/予測対実績/係数推移 | dashboard/pandas | Streamlit | artifact経由 | なし | artifactを読むだけで再計算しない。DBも使わない |
| `dashboard/factors.py` | BUY条件の表示と係数集計 | `load_configured_buy_rule`, `buy_rule_mismatches`, `summarize_coefficients` | `config/trading.yaml`、係数行 | `BuyRule`、安定性レポート | PyYAML/pandas/`scoring.stability` | `pages/3_Factor_Analysis.py` | `test_factors.py` | — | 表示専用read。strict検証は`data.config`が担当。設定と保存済み判定の食い違いを警告 |

## Scripts / automation

| Path | Purpose | API | Input | Output | Dependencies | Called by | Tests | Secrets | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `cli.py` | 全運用コマンドの単一入口 | `main`, subcommand dispatch | subcommand + そのscriptの引数 | delegateのexit code | 各`scripts/`モジュール | 人手/GitHub Actions | delegate側のtests | delegateと同一 | 業務ロジックを持たない薄いdispatch。`python -m cli <cmd> -- --help`でdelegateのhelpへ |
| `scripts/run_buy_all_reference.py` | 全銘柄購入の対照結果 | `main` | 期間/コスト | 集計とJSON | yfinance/`backtest.scenario` | `cli buy-all` | `test_scenario.py`(engine) | なし | モデル非使用。BUY判定の価値を測る基準。コスト未設定なら停止 |
| `scripts/run_week_test.py` | DBなしのwalk-forward検証 | `main` | 期間/学習窓 | `artifacts/week_test/latest.json` | yfinance/features/models/trading | `cli week-test` | 構成要素のtests | なし | **自銘柄特徴量と海外指標を1営業日ラグ**させリークを防ぐ。Provider品質ゲートとPIT lineageは通らない |
| `scripts/start_dashboard.sh` | Dashboardのローカル起動 | shell script | `--lan`任意 | Streamlit process | venv/.env/streamlit | 人手/LaunchAgent | manual | `DATABASE_URL` | `--lan`は同一ネットワークへ無認証公開。信頼できる回線のみ |
| `scripts/com.jpstock.dashboard.plist` | ログイン時のDashboard常駐 | launchd job | — | 常駐process + log | launchd/start_dashboard.sh | 利用者が手動install | manual | — | localhostのみ。Mac起動中だけ有効。外出先からはStreamlit Cloudを使う |
| `scripts/runtime.py` | config/env/engine/factory共通構築 | `load_runtime` | config dir/env | runtime tuple | config/env/database | operational scripts | indirect | `DATABASE_URL` + optional vars | engineをfinallyでdispose |
| `scripts/phase0_data_feasibility.py` | 無料coverage report | `build_report`, `main` | config/`--network` | JSON | data.fetch/Yahoo | user | config/provider tests | none; network Yahoo only | defaultはnetworkなし |
| `scripts/bootstrap_history.py` | 最大3年の初期raw取得 | `main` | from/to/config | fetch report/DB rows | `data.fetch` | user | fetch tests | `DATABASE_URL`, optional `EODHD_API_KEY` | current日を含めると未完了sessionに注意 |
| `scripts/run_morning_prediction.py` | 朝pipeline CLI | `main` | date/history/skip ingestion/dry-run | JSON + DB | `pipeline.morning` | morning Action/user | pipeline components | DB, optional EODHD | dry-runはconfig/DB設定確認だけでpublishしない |
| `scripts/send_morning_email.py` | persisted set email CLI | `main` | date/top N/dry-run | JSON preview/delivery | `services.email` | email Action/user | `test_email.py` | DB/email variables | dry-runはrenderのみでemail logをclaimしない |
| `scripts/update_open.py` | Open観測CLI | `main` | date/observed-at | JSON + PENDING actual | `pipeline.open` | user/future Action | indirect | DB | optional。Predictionを上書きしない |
| `scripts/run_close_update.py` | close pipeline CLI | `main` | date/observed-at/skip fetch | JSON + DB | `pipeline.close` | close Action/user | metric/trading tests | DB | raw未確定はPENDING |
| `scripts/run_walk_forward.py` | DB raw履歴のestimated-PIT OOS batch | `main` | date/ticker/output dir | 銘柄CSV + summary JSON | dataset/backtest/trading/metrics | user | `test_walk_forward.py`（pure core） | `DATABASE_URL` | 既定`artifacts/backtest`; DB metricへ保存しない |
| `.github/workflows/ci.yml` | pytest/Ruff/mypy/migration | Actions job | push/PR/manual | CI status | GitHub Actions | GitHub | itself | test `DATABASE_URL` only | Python 3.12 |
| `.github/workflows/morning_prediction.yml` | 08:20 JST予測（23:20 UTC） | scheduled/manual job | secrets/input date | DB publication | scripts | GitHub scheduler | manual dry-run/E2E pending | `DATABASE_URL`, optional `EODHD_API_KEY` | `AUTOMATION_ENABLED` gate。scheduler遅延あり |
| `.github/workflows/morning_email.yml` | 08:45/50/55 JST（前日23時UTC）email retry | scheduled/manual job | DB/email secrets | email log/delivery | scripts | GitHub scheduler | manual dry-run/E2E pending | DB/SMTP/Resend/email variables | Gmail exactly-onceは保証不可 |
| `.github/workflows/close_update.yml` | 15:45/55/16:10 actual更新 | scheduled/manual job | DB/date | actual/trade/metric | scripts | GitHub scheduler | E2E pending | `DATABASE_URL` | publication lagならPENDING |

## Tests / docs

| Path | Purpose | API | Input | Output | Dependencies | Called by | Tests | Secrets | Notes |
|---|---|---|---|---|---|---|---|---|---|
| `tests/test_*provider*.py`, `test_fetch.py`, `test_market_*.py`, `test_lookahead.py` | Provider/PIT/router/calendar/raw検証 | pytest tests/fixtures | mocks/config | pass/fail | pytest/respx | CI | self | dummyのみ | 実network SLAはtestしない |
| `tests/test_features.py`, `test_dataset.py`, `test_models.py`, `test_walk_forward.py` | leakage/feature/ML/OOS | pytest tests | synthetic frames/DB | pass/fail | pytest/pandas/sklearn | CI | self | — | future row不変とtraining endを検査 |
| `tests/test_trading.py`, `test_metrics.py`, `test_scoring.py` | signal/cost/metric式 | pytest tests | deterministic arrays | pass/fail | pytest/numpy | CI | self | — | paper-only |
| `tests/test_database.py`, `test_prediction_repository.py` | migration/model/repository constraint | pytest | SQLite/session | pass/fail | SQLAlchemy/Alembic | CI | self | test DB URL | PostgreSQL concurrency E2Eは別途必要 |
| `tests/test_email.py`, `test_dashboard.py` | render/idempotency/safe read UI | pytest | fake sender/SQLite | pass/fail | pytest/Streamlit modules | CI | self | dummy credentials | 実Gmail/Streamlit E2EはUser Action |
| `docs/*.md` | architecture/data/model/operations/deployment/status | — | 実装監査 | 説明書 | repository | user/developer | manual review | 変数名だけ | code変更時に更新 |

## 変更箇所の早見表

- 銘柄追加: `config/stocks.yaml`、indicator group、`dashboard/catalog.py`、config/provider tests。
- 指標追加: `config/indicators.yaml`。新Providerなら`data/providers/`とrouter tests。
- BUY/cost変更: `config/trading.yaml`、strategy version、walk-forward再計算。
- model/window変更: `config/model.yaml`、model/feature version、OOS再計算。
- メール文面変更: `notifications/templates.py`。template versionとemail testsも変更。
- Dashboard変更: 対応する`pages/`、`dashboard/query_service.py`、`test_dashboard.py`。
- schedule変更: `.github/workflows/`と`config/settings.yaml`の双方。
- Backtest画面の可変条件追加: `backtest/scenario.py`の`ScenarioConfig`、`pages/5_Backtest.py`の入力、`test_scenario.py`。
- 回帰候補追加: `models/linear.py`にpipeline、`models/base.py`の`REGRESSION_CANDIDATES`とgrid、`models/comparison.py`の`_candidate_grid`/`_build_candidate`、`test_model_comparison.py`。
- Dashboardの見方の説明: `docs/DASHBOARD_GUIDE.md`。

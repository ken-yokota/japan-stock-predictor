# Expected Repository Files Guide

このファイルは、Codexへ実装依頼した際に作成させる主要ファイルの「期待される役割」をまとめたものです。
実際の実装後は、Codex自身に `docs/FILES.md` をリポジトリの実ファイルに合わせて更新させてください。

| Path | 役割 |
|---|---|
| `README.md` | プロジェクト概要、Quick Start、実行・テスト・デプロイ手順 |
| `.env.example` | 必要な環境変数名だけを記載。秘密値は入れない |
| `config/stocks.yaml` | 対象日本株、業種、ProviderごとのSymbol |
| `config/indicators.yaml` | 海外指標、Provider、品質、Sector対応、ON/OFF |
| `config/model.yaml` | 120営業日window、モデル、CV、特徴量関連設定 |
| `config/trading.yaml` | BUY閾値、投資額、手数料、slippage |
| `src/.../providers/` | Yahoo/Treasury/EODHD等のデータ取得抽象化 |
| `src/.../db/` | SQLAlchemy model、session、repository、migration/init |
| `src/.../features/` | Return、Volatility、Yield curve等の特徴量生成 |
| `src/.../models/` | Ridge、ElasticNet、Logistic、時系列CV |
| `src/.../backtest/` | 120営業日rollingのWalk-Forward OOS |
| `src/.../trading/` | BUY判定、Shares、P/L、cost |
| `src/.../metrics/` | PF、Win Rate、Expectancy、Sharpe、Sortino等 |
| `src/.../scoring/` | Readability、Confidence、Coefficient Stability |
| `src/.../email/` | ResendメールHTML/Text、dry-run、idempotency |
| `src/.../services/` | Morning/Open/Close等の日次ワークフロー統合 |
| `app.py` | Streamlitエントリーポイント |
| `pages/1_Today.py` | 今日の予測・BUYランキング |
| `pages/2_Stock_Detail.py` | 銘柄別予測履歴、損益、係数 |
| `pages/3_Factor_Analysis.py` | 指標寄与、係数、安定性 |
| `pages/4_Sector_Analysis.py` | 業種別比較 |
| `pages/5_Backtest.py` | 閾値・投資額等を変更したOOS再計算 |
| `pages/6_System_Status.py` | Provider/DB/Workflow/欠損/エラー状態 |
| `scripts/phase0_data_feasibility.py` | 無料データの取得可否・08:30利用可否検証 |
| `scripts/bootstrap_history.py` | 2〜3年を目標に履歴データを初期取得 |
| `scripts/run_walk_forward.py` | OOS walk-forward一括作成 |
| `scripts/run_morning_prediction.py` | 08:20処理 |
| `scripts/send_morning_email.py` | 08:45メール |
| `scripts/update_open.py` | 寄り付き後Actual Open更新 |
| `scripts/run_close_update.py` | 15:45以降の実績・損益更新 |
| `.github/workflows/morning_prediction.yml` | 朝予測の自動実行 |
| `.github/workflows/morning_email.yml` | 朝メールの自動実行 |
| `.github/workflows/close_update.yml` | 大引け後更新 |
| `tests/` | Leakage、特徴量、ML、損益、スコア、重複実行等のpytest |
| `docs/FILES.md` | **実際に作成された全主要ファイルの説明** |
| `docs/ARCHITECTURE.md` | データフローとシステム構成 |
| `docs/DATA_SOURCES.md` | Provider、Symbol、Timezone、Delay、Quality |
| `docs/DATA_DICTIONARY.md` | DB列、特徴量、単位、timestamp定義 |
| `docs/PHASE0_DATA_FEASIBILITY.md` | 各指標の無料取得可否とTier判定 |
| `docs/MODELING.md` | Target、120営業日window、時系列CV、係数 |
| `docs/BACKTEST.md` | Walk-Forward OOSと売買評価方法 |
| `docs/METRICS.md` | PF/勝率/Expectancy/Readability/Confidence等の数式 |
| `docs/OPERATIONS.md` | 毎日の08:20→08:45→09:00→15:45運用 |
| `docs/DEPLOYMENT.md` | Streamlit/GitHub Actions/PostgreSQL/Secrets |
| `docs/ASSUMPTIONS.md` | 実装時にCodexが置いた合理的Default |
| `docs/KNOWN_ISSUES.md` | 無料データやスケジューラ等の限界 |
| `docs/IMPLEMENTATION_REPORT.md` | 完了項目、テスト結果、残課題、User Action |

## 特に重要

`docs/FILES.md` は「ファイル名だけの一覧」にせず、各ファイルごとに以下を説明する想定です。

- Path
- Purpose
- Main classes/functions
- Input
- Output
- Dependencies
- Called by
- Related tests
- Secrets used（環境変数名だけ）
- Notes / operational caution

これにより、後から「メールを変えたい」「銘柄を増やしたい」「指標を追加したい」「BUY条件を変えたい」ときに、どこを修正すればよいか分かる状態にします。

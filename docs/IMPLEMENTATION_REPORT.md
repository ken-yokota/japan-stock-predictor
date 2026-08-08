# 実装レポート

更新日: 2026-08-08

## 実装済み

- 日本株22銘柄、海外37指標、model/trading/scheduleのstrict YAML設定
- `MarketDataProvider`、Yahoo Finance、U.S. Treasury、EODHD Free provider
- timeout/retry、EOD単一Provider routing、snapshot freshness、08:30 PIT gate
- raw OHLC/Adjusted Close、provider/timestamp/quality/revision、取得・選択監査
- PostgreSQL SQLAlchemy schemaとAlembic migration 0001〜0003
- price/Treasury feature、raw-row lineage、銘柄別120-session dataset
- training-only imputation/scaling、Ridge、Logistic、TimeSeriesSplit selection
- one-step walk-forward純粋計算、BUY、100株lot、two-sided cost paper simulation
- PF/Expectancy/Sharpe/Sortino/drawdown/correlation/readability/confidence/stability計算
- 朝のingestion→feature→model→PredictionSet一括公開pipeline
- Gmail SMTP primary、Resend optional、dry-run、HTML/Text、DB email log統合
- Streamlit entry pointとToday/Stock/Factor/Sector/Backtest/System Statusのread-only画面
- CI、および朝予測・朝メール・close updateのworkflow定義
- Phase 0、bootstrap history、morning/email/open/close、estimated-PIT walk-forwardのscript

## 未完了 / 未稼働

- walk-forward CSV/JSON artifactをversioned DB tableへ一括永続化するintegration。現在のbatchはartifact出力まで。
- actual Open取得後に朝のreference priceとpredicted closeを表示更新するintegration。現在はActual Openを別のPENDING outcomeとして保存する。
- Neon本番migration/concurrency、Streamlit deployment、Gmail実配信、連続営業日の08:20運用。
- 無料データだけでの十分なOOS期間を蓄積し、有効性を判断する作業。
- Yahoo等の公開/メール再配布許諾確認。

## 検証

統合commit `f8507ec` のGitHub Actions（Python 3.12）で、127 pytest、Ruff、全72 source fileのstrict mypy、SQLite Alembic `upgrade head` / `check` が成功した。別jobのPostgreSQL 16 serviceでもfresh databaseへの`upgrade head` / `alembic check`が成功した。

確認run: <https://github.com/ken-yokota/japan-stock-predictor/actions/runs/31251766101>

ローカルの分離venv（Python 3.11、production基準ではない）でも127 pytestを完走した。本番基準は上記Python 3.12 CIを正とする。

## User Action

1. Neon Free PostgreSQLを作成し、GitHub/Streamlitへ`DATABASE_URL`をsecret登録する。
2. Gmail 2FA + App Passwordを作り、SMTP/送受信addressをGitHub secretsへ登録する。
3. Streamlit Community Cloudで`app.py`をdeployし、URLを`APP_URL`へ登録する。
4. Actionsを手動dry-runし、DB、Dashboard、メール1通を確認する。
5. close updateを実データで手動確認してから`AUTOMATION_ENABLED=true`を設定する。
6. 2〜3年を目標にbootstrapし、walk-forward OOSのsample数・PF・drawdown・provider品質を確認する。

## 完了判定

「アプリが完成」とするには、朝予測だけでなく、実Neon、実Streamlit、実メール、close答え合わせ、OOS metrics、scheduled retryのすべてが連続営業日で動作し、data quality警告を利用者が確認できる必要がある。現在はコード基盤が大きく進んだ段階であり、その運用完了条件にはまだ達していない。

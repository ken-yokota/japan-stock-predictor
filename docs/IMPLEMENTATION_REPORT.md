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
- ElasticNet / OLS / Lassoのpipelineと、同一TimeSeriesSplit foldでの回帰候補比較
- one-step walk-forward純粋計算、BUY、100株lot、two-sided cost paper simulation
- PF/Expectancy/Sharpe/Sortino/drawdown/correlation/MAE/RMSE/readability/confidence/stability計算
- 朝のingestion→feature→model→PredictionSet一括公開pipeline
- Actual Open取得後の`actual_open × (1 + predicted_return)`派生表示
- Gmail SMTP primary、Resend optional、dry-run、HTML/Text、DB email log統合
- Streamlit entry pointとToday/Stock/Factor/Sector/Backtest/System Statusのread-only画面
- Backtest画面での閾値・投資額・コスト・Top N変更によるOOS再計算
- 全運用コマンドの単一入口`cli.py`とDashboard起動script / LaunchAgent
- Factor Analysis画面のBUY条件表示と、設定と保存済み判定の食い違い警告
- 約6か月分の係数を平均・符号一致率・安定性で集計する画面
- DBなしで直近期間を検証する`cli week-test`と、その結果を表示する「テスト」画面
- BUY判定の価値を測る対照ケース`cli buy-all`（全銘柄を毎日購入）
- CI、および朝予測・朝メール・close updateのworkflow定義
- Phase 0、bootstrap history、morning/email/open/close、estimated-PIT walk-forwardのscript

## 未完了 / 未稼働

- walk-forward CSV/JSON artifactをversioned DB tableへ一括永続化するintegration。現在のbatchはartifact出力まで。
- 回帰候補比較を朝のpipelineへ組み込み、model昇格を自動化する運用。現在は診断API止まりで、productionはRidge固定。
- Actual Open基準のpredicted closeはDashboardの派生表示で、DBのpredictions行へは書き戻していない。08:30公開レコードをPIT証跡として不変に保つための意図的な選択である。
- Neon本番migration/concurrency、Streamlit deployment、Gmail実配信、連続営業日の08:20運用。
- 無料データだけでの十分なOOS期間を蓄積し、有効性を判断する作業。
- Yahoo等の公開/メール再配布許諾確認。

## 2026-08-03〜08-07の検証結果

`cli week-test`（DBを使わない研究用経路）で、各予測日の直前120営業日を学習し
5営業日を予測した結果。

| 指標 | BUY判定あり | 全銘柄を毎日購入（対照） |
|---|---|---|
| 取引数 | 3 | 110 |
| 勝率 | 66.7% | 55.5% |
| 金額ベース勝率 | 1.168 | 1.259 |
| 純損益 | +1,287円 | +145,460円 |
| 1取引あたり | +429円 | +1,322円 |

予測は110件作られたがBUY条件（予測リターン > 0.30% かつ 上昇確率 >= 60%）を満たしたのは
8月6日の3件だけだった。全予測の方向的中率は57.3%。

**この週については、BUY判定による絞り込みが価値を生んでいない。** 1取引あたりの利益は
無条件購入の3分の1以下で、市場全体が上昇した週に取引をほとんど見送っている。ただしBUY 3件は
統計的に何も結論できない数であり、優劣の判断材料にはならない。

## 検証

2026-08-08のローカル実行（Python 3.11の分離venv）で以下を確認した。

- `pytest -q`: **176 passed**（従来127から49件追加）
- `ruff check .`: All checks passed
- `ruff format --check .`: 差分なし
- `mypy ... cli.py`: **Success: no issues found in 80 source files**（CIと同じ範囲に`cli.py`を追加）
- ローカルPostgreSQL 16.14で`alembic upgrade head`と`alembic check`が成功し、20テーブルを作成

Python 3.11はproduction基準ではない。本番基準はPython 3.12のGitHub CIであり、SQLite/PostgreSQLのAlembic `upgrade head` / `check`を含む。統合commit `f8507ec`時点のCI実行は
<https://github.com/ken-yokota/japan-stock-predictor/actions/runs/31251766101>。
今回の変更に対応するCI実行はpush後に確認が必要である。

### 実行環境の注意

このMacでは`websockets`パッケージの初回読み込みで約93秒のI/O停止が起き（CPU 0%）、`yfinance`経由でimportするpytestが停止したように見えた。次のコマンドで一度キャッシュを温めれば解消する。

```bash
find .venv/lib/python3.11/site-packages/websockets -type f -exec cat {} + > /dev/null
```

これはコードの不具合ではなく、macOS側のファイルスキャンによるものである。

## テスト内訳（今回追加分）

| ファイル | 件数 | 検証内容 |
|---|---|---|
| `tests/test_model_comparison.py` | 7 | ElasticNet/Lasso/OLSのfit、4候補の順位付け、RMSE整合、分割不能sampleの`NOT_EVALUATED`、未知候補の拒否、決定性 |
| `tests/test_scenario.py` | 15 | 閾値の上下、Top N、コスト、資金と100株lot、LOW_SAMPLE、selection bias警告、空入力、非正Openの除外、確率閾値の境界、入力検証 |
| `tests/test_post_open.py` | 10 | Open基準のpredicted close、負のreturn、7種類の不正入力で`None`、Today表のPENDING/導出値 |
| `tests/test_dashboard.py` | +1 | 別プロセスでDashboard importがsklearn/yfinance/httpx/smtplib/servicesを引き込まないことを実証 |

## User Action

1. Neon Free PostgreSQLを作成し、GitHub/Streamlitへ`DATABASE_URL`をsecret登録する。
2. Gmail 2FA + App Passwordを作り、SMTP/送受信addressをGitHub secretsへ登録する。
3. Streamlit Community Cloudで`app.py`をdeployし、URLを`APP_URL`へ登録する。
4. Actionsを手動dry-runし、DB、Dashboard、メール1通を確認する。
5. close updateを実データで手動確認してから`AUTOMATION_ENABLED=true`を設定する。
6. 2〜3年を目標にbootstrapし、walk-forward OOSのsample数・PF・drawdown・provider品質を確認する。

いずれもアカウント作成・課金同意・秘密情報の登録を含み、コード側からは代行できない。

## 完了判定

「アプリが完成」とするには、朝予測だけでなく、実Neon、実Streamlit、実メール、close答え合わせ、OOS metrics、scheduled retryのすべてが連続営業日で動作し、data quality警告を利用者が確認できる必要がある。コードとテストはDefinition of Doneの項目を満たしたが、実データでの連続運用実績はまだ無い。

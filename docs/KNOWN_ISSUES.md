# 既知の制約と未完了事項

更新日: 2026-08-08

## 無料データ

- yfinance/Yahooは取引所公式APIではなく、非公式・best effortである。symbol、schema、rate limit、delay、継続提供が予告なく変わり得る。
- 無料snapshotのリアルタイム性を保証しない。FX/先物も取得時刻とmarket timestampを検査し、staleなら除外する。
- Yahooのhistorical backfillには各revisionの当時の公開/初観測時刻がない。historical PITは推定を含む。
- U.S. Treasury値は公式だが、historical XMLの各行に実公表時刻がない。
- EODHD Freeはquotaと履歴範囲が小さく、日本株やliveの一般fallbackではない。
- Iron Oreは許諾・symbol・08:30 PITを確認できる無料sourceが未解決。Baltic DryはBDRY ETF proxy、Capesize/Panamaxは未設定。
- corporate action、delisting、survivorship、寄り付きauction、limit up/down、流動性、税金を完全には再現しない。
- 公開Dashboardや第三者メールでの再配布権は未確認。個人利用の範囲を越える前に最新規約確認が必要。

## モデル・評価

- productionモデルはRidge/Logisticだけである。ElasticNet/OLS/Lassoは`models/comparison.py`で同一TimeSeriesSplit foldの診断比較として実装済みだが、朝のpipelineはRidgeで予測する。比較結果によるproduction昇格は自動化していない。比較スコアを投資成績として読まないこと。
- prediction intervalはtraining residual SDによる簡易幅で、統計的coverage保証はない。
- feature係数は相関的感応度であり因果関係ではない。
- 120-session windowと22銘柄はsampleが小さい。market regime changeに弱い。
- 無料historical PIT推定のため、backtestが運用実績より良く見える可能性がある。
- thresholdをDashboardで試行して良いものだけ採用するとselection biasが生じる。
- walk-forward batchはCSV/JSON artifactを出力するが、その結果をversioned DB tableへ永続化しない。DashboardのBacktest画面はDBのlive OOS metric/tradeを表示し、artifactを直接読まない。
- Backtest画面の再計算タブは、DBの確定済みOOS予測へ売買条件だけを再適用する。modelと学習期間は予測自体を作り直す必要があるため画面から変更できず、`python -m cli walk-forward`の再実行が必要である。同じ履歴に対して条件を何通りも試せるため、画面が試行回数を数えて警告するが、良い条件だけを採用すればselection biasは残る。

## 運用

- GitHub Actions scheduleはbest effortで、08:20/08:45/15:45ちょうどの開始を保証しない。遅延時もcutoffは08:30固定。
- Gmail SMTPはprovider側idempotency APIがない。DB claimで通常のretry重複は防ぐが、「SMTP受理直後に接続断」のような曖昧なfailureでは厳密なexactly-onceを保証できない。
- Resendはoptionalで、完全無料の継続やquotaを本システムが保証しない。
- close/update_open pipelineは実装済みだが、実Yahoo publication lagとNeon上の連続営業日E2Eは未検証。
- `update_open.py`のOpenはYahooで最初に観測したregular-session 1分bar由来で、取引所公式auction値を保証しない。Actual OpenはPENDING outcomeとして保存し、朝の前日終値referenceを上書きしない。`config/trading.yaml`の`recompute_after_actual_open`はDashboard側の派生表示として実装済みで、Todayに`実績Open`と`予測終値(Open基準)`を出す。Openが未取得の間は`PENDING`のままで、前日終値ベースの値をOpen基準として表示しない。DBの朝レコードは08:30時点のPIT証跡として不変に保つため、この派生値をpredictions表へ書き戻していない。
- CIのPostgreSQL 16ではfresh migrationを検証済み。実Neonでの接続、concurrency、長期運用はUser Action後に検証が必要。
- Streamlit Community Cloudのsleep、resource、private repository policyはサービス側変更の影響を受ける。
- DashboardのSystem StatusはDBの最後の監査情報で、live Provider pingではない。

## Packaging / validation

Python 3.12以上が必要。開発PCに3.11の既存venvがある場合、新しい3.12環境を作る。全packageのeditable install、127 pytest、Ruff、strict mypy、SQLite/PostgreSQL migrationはPython 3.12 GitHub CIで検証済みである。

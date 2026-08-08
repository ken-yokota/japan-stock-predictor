# アーキテクチャ

更新日: 2026-08-08

## 目的と境界

本システムは、予測日の08:30 JSTまでに利用可能だった無料データだけで、日本株22銘柄の寄り付きから大引けまでのリターンを研究する。売買注文は行わず、DBに保存する取引はすべてpaper simulationである。

市場価格のbusiness logicは具体的なYahoo実装へ依存せず、`MarketDataProvider`と`ProviderRouter`を境界にする。現在の主要経路はYahoo Finance、米国金利はU.S. Treasury、EODHD Freeはquotaを制限した任意fallbackである。将来は同じ境界へ有料Providerを追加できる。

## 全体フロー

```text
GitHub Actions 08:20 JST
  -> 無料Provider取得
  -> raw OHLCV + 6種の時刻 + 品質 + Provider監査をPostgreSQLへ保存
  -> 08:30固定cutoffでPIT datasetを構築
  -> 銘柄別Ridge + Logisticを120 JPX営業日で学習
  -> PredictionSetを一括公開

GitHub Actions 08:45/08:50/08:55 JST
  -> READY PredictionSetをDBから読む
  -> Gmail SMTP（primary）またはResend（optional）
  -> email_logsで重複送信を抑止

Streamlit
  -> PostgreSQLをread-only query
  -> 予測、係数、Provider品質、OOS結果を表示

GitHub Actions 15:45/15:55/16:10 JST
  -> 当日Open/Closeを取得・確定
  -> Actual / paper trade / live OOS metricsを更新
```

朝予測、任意の寄り付き観測、大引け後のactual・trade・live OOS metricを永続化する処理は実装済みである。過去全期間のwalk-forward一括処理は別入口で扱う。UIは既存データだけを表示し、画面表示時にProviderやモデルを呼ばない。

## レイヤー

| レイヤー | 主な場所 | 責務 |
|---|---|---|
| Configuration | `config/`, `data/config.py`, `data/env.py` | YAMLのstrict検証、環境変数、provider/model/trading default |
| Provider | `data/providers/` | Yahoo、Treasury、EODHD Freeの取得とprovider-neutralな型への変換 |
| Quality / PIT | `data/provider_router.py`, `data/alignment.py`, `data/snapshot.py`, `data/availability.py` | cutoff、coverage、鮮度、単一Provider選択、look-ahead拒否 |
| Persistence | `database/`, `alembic/` | raw revision、lineage、model、prediction、actual、paper trade、metric、email監査 |
| Feature / ML | `features/`, `models/`, `services/dataset.py`, `services/prediction.py` | 特徴量、training-only imputation/scaling、時系列CV、予測 |
| Application | `services/`, `pipeline/`, `notifications/` | 朝処理、永続化、メール構築・送信 |
| Presentation | `dashboard/`, `app.py`, `pages/` | DB read-only表示、状態をREADY/PENDING/ERRORで明示 |
| Automation | `.github/workflows/`, `scripts/` | CIと日次エントリーポイント |

## Provider差し替え

`MarketDataProvider.fetch_eod()`は`FetchRequest`を受け、共通`MarketBar`を返す。snapshot対応Providerは`SnapshotMarketDataProvider`も満たす。`ProviderRouter`へregistry keyと優先順を渡し、候補ごとに品質、PIT、必要sessionのcoverageを判定する。EODの不足日だけ別Providerで埋めることはない。

有料Provider追加時は、次の順に変更する。

1. `MarketDataProvider`を実装する。
2. provider symbolを`config/indicators.yaml`へ追加する。
3. registryを構成するサービスへ注入する。
4. `provider_attempts`と`provider_selections`が新Providerでも記録されることをtestする。
5. 旧Providerと同一時刻・同一instrumentの比較を行い、品質labelを決める。

## 不変条件

- 予測cutoffは常に予測日08:30 JSTで、ジョブの開始・遅延時刻へ追従しない。
- operational score行は`market_timestamp <= available_timestamp <= cutoff_at`、`first_observed_at <= cutoff_at`、`retrieved_at <= cutoff_at`を満たす。
- 当日株価Open/Closeは予測特徴量に入れず、後からoutcomeにだけ使う。
- モデル、imputer、scaler、hyperparameter selectionは銘柄別かつtraining fold内でfitする。
- `PredictionSet`は全銘柄を保存してから`READY`または`INSUFFICIENT_DATA`へ確定する。
- Dashboardはread-only。API key、DB URL、recipient、SMTP errorの生値を表示しない。
- paper tradeは`is_simulated=true`で、自動発注コードを持たない。

## 冪等性と障害時

raw行はProvider・symbol・interval・market timestamp・raw hashでrevisionを識別する。feature/model/prediction/emailはidempotency keyを持つ。朝処理は同じ日・同じversionの確定済みPredictionSetがあれば再利用する。

メールはDB claim後に送信するため並行retryによる通常の重複を抑止する。ただしSMTPはProvider側のidempotency APIを持たない。SMTPサーバが受信した直後に接続が切れた場合、クライアントは配信成否を断定できず、厳密なexactly-onceは保証できない。Resend選択時はprovider側idempotency keyも送る。

## セキュリティ

秘密値は環境変数だけから読み、repositoryへcommitしない。本番DBはNeon等のPostgreSQLを想定する。Streamlit Community CloudとGitHub Actionsには別々に必要なsecretを登録する。ログとUIには例外型またはsanitized messageだけを出す。

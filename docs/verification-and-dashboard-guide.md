# 確認・ダッシュボード・アーキテクチャガイド

更新日: 2026-08-08
対象リポジトリ: `japan-stock-predictor`

## 1. このガイドの目的

この文書では、次の内容を確認できます。

- 現在どこまで実装されているか
- 設定、Provider、データ取得、08:30時点判定をどう確認するか
- PostgreSQLに保存されたデータとProvider選択をどう確認するか
- システムのアーキテクチャとデータの流れ
- 現在実装済みの計算方法
- 今後実装する予測、バックテスト、ダッシュボードの見方
- 異常、欠損、遅延、fallbackをどう判断するか

このシステムは投資分析・研究用です。予測や利益を保証するものではなく、自動発注も
行いません。

## 2. 現在の実装状況

現在は元仕様の **Phase 1** まで実装されています。

| 機能 | 状態 | 現在確認できるもの |
|---|---|---|
| YAML設定 | 実装済み | 日本株22銘柄、37指標、Provider優先順位、鮮度上限 |
| Yahoo Finance Provider | 実装済み | 日本株・海外EOD・08:30 snapshot |
| U.S. Treasury Provider | 実装済み | 2Y・10Y・30Y、10Y−2Y、1/3/5観測日変化 |
| EODHD Free Provider | 実装済み | 任意のEOD fallback、symbol確認、Yahoo比較 |
| 08:30 PIT・鮮度判定 | 実装済み | cutoff、取得時刻、未来値、stale、coverage検査 |
| PostgreSQL / Alembic | 実装済み | rawデータ、run、Provider試行・選択監査 |
| 特徴量・予測モデル | 未実装 | Phase 2で実装予定 |
| Walk Forward・バックテスト | 未実装 | Phase 2以降で実装予定 |
| Streamlit Dashboard | 未実装 | Phase 4で実装予定 |
| メール・定期実行 | 未実装 | Phase 5で実装予定 |

したがって、現時点では `streamlit run app.py` を実行してもダッシュボードは表示できません。
`app.py`と`pages/`はまだ作成されていません。この文書のダッシュボード節は、今後の
実装時に使う画面仕様・確認基準です。

## 3. 最短の確認手順

### 3.1 プロジェクトへ移動

```bash
cd /Users/yokotaken/Desktop/japan-stock-predictor
source .venv/bin/activate
```

新しく環境を作る場合はPython 3.12以上を使用します。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

### 3.2 設定を確認

ネットワーク、DB、API keyなしで実行できます。

```bash
python -m data.fetch config-check
```

期待する主要値は次のとおりです。

```json
{
  "status": "OK",
  "primary_provider": "yahoo_finance",
  "stocks": 22,
  "historical_indicators": 17,
  "snapshot_indicators": 12,
  "treasury_tenors": 3,
  "unresolved_required_indicators": ["iron_ore"]
}
```

`iron_ore`は現在未解決です。架空値や無関係な値では補完しません。
`status: OK`はYAML設定が妥当という意味だけで、予測や本番運用の準備完了を意味しません。

別の設定directoryを確認する場合、global optionはsubcommandより前に置きます。

```bash
python -m data.fetch --config-dir /path/to/config config-check
```

### 3.3 自動テストを確認

```bash
pytest -q
ruff check .
mypy data database
```

現在の期待結果は次のとおりです。

```text
77 passed
All checks passed!
Success: no issues found
```

## 4. 環境変数とDBの準備

`.env`には少なくとも取得CLI用の`DATABASE_URL`を設定します。

```dotenv
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/japan_stock_predictor
TIMEZONE=Asia/Tokyo

# 任意。空ならEODHD fallbackは無効です。
EODHD_API_KEY=
```

Yahoo FinanceとU.S. TreasuryにはAPI keyは不要です。`EODHD_API_KEY`を設定しなくても
Yahoo + Treasuryの無料構成で動きます。

ローカルPostgreSQLをDockerで起動する場合:

```bash
docker compose up -d postgres
alembic upgrade head
```

状態確認:

```bash
docker compose ps
alembic current
alembic check
```

この開発環境ではSQLiteによるmigration往復確認まで実施済みですが、実PostgreSQL serverでの
integration確認は未実施です。上記Docker手順または実`DATABASE_URL`で別途確認してください。

## 5. Providerの接続確認

### 5.1 Yahoo Finance

```bash
python -m data.fetch verify-yahoo
```

確認ポイント:

- `provider`が`yahoo_finance`
- `health`が`OK`
- 各symbolが`VERIFIED`
- `MISSING`または`NOT_FOUND`があれば、symbol変更、休日、Yahoo障害を確認

これは取引所による公式symbol認証ではなく、価格取得によるbest-effort確認です。

### 5.2 U.S. Treasury

公式XML feedへの接続だけを確認する場合:

```bash
python - <<'PY'
from data.providers import TreasuryProvider

with TreasuryProvider() as provider:
    print(provider.healthcheck())
PY
```

`ok=True`なら公式feedへ接続できています。実データは`fetch-free`実行時に取得します。

### 5.3 EODHD Free（任意）

`.env`にFree Planのkeyを設定した場合だけ実行します。

```bash
python -m data.fetch verify-eodhd
```

本アプリはEODHDをPrimaryにしません。Free枠保護のため、1実行あたり最大5 API callsに
制限しています。

同じ上場商品のYahoo終値とEODHD終値を比較する場合:

```bash
python -m data.fetch compare-eod \
  --from-date 2026-08-01 \
  --to-date 2026-08-07 \
  --max-series 5
```

指数とETFのように価格水準を直接比較できない組合せは自動的に除外されます。
`compare-eod`は診断用の直接比較で、DB保存、08:30 PIT router、Provider選択監査は
通りません。比較結果をそのまま予測入力として扱わないでください。

## 6. データ取得の確認

### 6.1 履歴データだけを取得

`--to-date`には、予測cutoffより前に完了している最後の対象sessionを指定します。
例えば2026-08-10朝の予測準備なら、週末前の2026-08-07までです。

```bash
python -m data.fetch fetch-free \
  --from-date 2026-01-01 \
  --to-date 2026-08-07
```

### 6.2 08:30 snapshotも取得

実運用では08:20〜08:30 JSTの間に実行します。

```bash
python -m data.fetch fetch-free \
  --from-date 2026-01-01 \
  --to-date 2026-08-07 \
  --prediction-date 2026-08-10 \
  --include-snapshots
```

08:30を過ぎてから同じコマンドを実行した場合、後から取得したsnapshotを08:30時点の
情報として採用しません。`AFTER_CUTOFF`または`skipped_sources`になるのが正しい動作です。
現CLIでは`--include-snapshots`を付けたときに、EODやTreasuryにも運用時の追加cutoff判定が
有効になります。独立した`--operational-run` optionはありません。

### 6.3 取得レポートの見方

| 項目 | 意味 |
|---|---|
| `status` | `SUCCESS`、`PARTIAL`、`FAILED` |
| `requested_sources` | 計画上の取得対象数 |
| `succeeded_sources` | gateを通過したsource数 |
| `inserted_rows` | 新規に保存したrevision数 |
| `reused_rows` | 同一raw hashとして再利用した行数 |
| `failed_sources` | Providerエラー、coverage不足、応答異常 |
| `skipped_sources` | 未要求snapshot、stale、cutoff後取得など |
| `unresolved_required` | 現在は主に`iron_ore` |
| `selected_providers` | 系列ごとに最終採用したraw Provider |

`PARTIAL`は必ずしもプログラム障害ではありません。例えばsnapshotを要求しなかった、
Iron Oreが未解決、取得値が鮮度基準を満たさなかった場合も安全側に`PARTIAL`となります。
CLI reportの`skipped_sources`や`unresolved_required`は、現在のDBへすべて同じ粒度では
保存されません。調査時はCLIのJSON出力も保存してください。

## 7. PostgreSQLでの確認方法

現在のDB headに存在する表は次の7つです。

- `market_data`
- `stock_prices`
- `instrument_mappings`
- `daily_runs`
- `ingestion_batches`
- `provider_attempts`
- `provider_selections`

features、predictions、model artifacts、backtest、trades、email用テーブルはまだありません。
また、現CLIの`ingestion_batches`はProviderごとではなく、1 runにつき
`free_provider_stack`として1件作成されます。

Docker PostgreSQLへ入る例:

```bash
docker compose exec postgres \
  psql -U postgres -d japan_stock_predictor
```

### 7.1 最新run

```sql
SELECT
    run_id,
    prediction_date,
    cutoff_at,
    status,
    current_step,
    started_at,
    finished_at,
    failed_symbols
FROM daily_runs
ORDER BY started_at DESC
LIMIT 10;
```

### 7.2 採用Provider

```sql
SELECT
    run_id,
    canonical_symbol,
    interval,
    selected_registry_key,
    selected_provider,
    selection_role,
    data_quality,
    freshness_status,
    coverage,
    details
FROM provider_selections
ORDER BY selected_at DESC
LIMIT 100;
```

`details`の`is_proxy=true`なら、指数そのものではなくETF等の代理系列です。
この選択監査は現在、routerを通るYahoo/EODHDの市場価格系列が対象です。Treasury取得は
別経路のため、`provider_selections`には記録されません。

### 7.3 Provider候補の失敗理由

```sql
SELECT
    run_id,
    canonical_symbol,
    registry_key,
    priority,
    accepted,
    data_quality,
    freshness_status,
    coverage,
    reason ->> 'message' AS reason
FROM provider_attempts
ORDER BY attempted_at DESC, canonical_symbol, priority
LIMIT 200;
```

### 7.4 海外データとsnapshot

```sql
SELECT
    canonical_symbol,
    symbol,
    provider,
    interval,
    market_date,
    market_timestamp,
    available_timestamp,
    first_observed_at,
    retrieved_at,
    data_quality,
    is_realtime,
    is_delayed,
    close,
    quality_flags
FROM market_data
ORDER BY retrieved_at DESC
LIMIT 100;
```

### 7.5 日本株

```sql
SELECT
    canonical_symbol,
    symbol,
    provider,
    market_date,
    open,
    high,
    low,
    close,
    adjusted_close,
    volume,
    available_timestamp,
    data_quality
FROM stock_prices
ORDER BY market_date DESC, canonical_symbol
LIMIT 100;
```

### 7.6 時刻関係の異常がないことを確認

次の件数は0である必要があります。

```sql
SELECT COUNT(*) AS invalid_timestamp_rows
FROM market_data
WHERE market_timestamp > available_timestamp
   OR available_timestamp > first_observed_at
   OR first_observed_at > retrieved_at
   OR retrieved_at > last_seen_at;
```

```sql
SELECT COUNT(*) AS invalid_timestamp_rows
FROM stock_prices
WHERE market_timestamp > available_timestamp
   OR available_timestamp > first_observed_at
   OR first_observed_at > retrieved_at
   OR retrieved_at > last_seen_at;
```

## 8. アーキテクチャ

実線はPhase 1で実装済み、点線は今後のPhaseです。

```mermaid
flowchart LR
    C[config YAML] --> P[Fetch Plan]
    P --> R[Market Provider Registry / Router]

    Y[YahooFinanceProvider] --> N[MarketBar Normalizer]
    E[EODHDFreeProvider] --> N

    N --> R
    R --> G[PIT / Quality / Freshness / Coverage Gates]
    G --> A[Provider Attempts & Selection Audit]
    G --> D[(PostgreSQL)]
    A --> D

    T[TreasuryProvider] --> TN[Treasury Normalize / Derive]
    TN --> TG[Operational Eligibility Check]
    TN --> D
    TG --> CR[CLI Report]

    D -. Phase 2 .-> F[Features]
    F -.-> M[Models / Walk Forward]
    M -. Phase 3 .-> Q[Metrics / Readability]
    D -. Phase 4 .-> QS[Read-only Query Service]
    Q -.-> QS
    QS -.-> S[Streamlit Dashboard]
    Q -. Phase 5 .-> L[Email / Scheduler]
```

Treasury raw/derived行は現在、運用適格性の判定より前にDBへ保存されます。したがって、
「DBに存在する」ことと「その日の08:30予測へ使用できる」ことは同じではありません。
利用時にはcutoff適格性を別途確認します。

### 8.1 レイヤー別の責務

| レイヤー | 主なファイル | 責務 |
|---|---|---|
| Config | `config/*.yaml`, `data/config.py` | 銘柄、指標、symbol、優先順位、鮮度を検証 |
| Provider | `data/providers/` | HTTP/yfinance取得、応答検証、共通MarketBar化 |
| Routing | `data/provider_router.py` | Yahoo/EODHD候補を評価し、単一Providerを選択 |
| PIT | `data/availability.py`, `data/snapshot.py`, `data/alignment.py` | cutoff、鮮度、as-of選択、look-ahead防止 |
| Persistence | `database/` | revision、run、attempt、selectionを保存 |
| Migration | `alembic/` | PostgreSQL schemaの更新 |
| CLI | `data/fetch.py` | 設定確認、Provider確認、取得処理を統合 |

市場価格のビジネスロジックは`YahooFinanceProvider`を直接前提にせず、
`Mapping[str, MarketDataProvider]`のregistryからProviderを解決します。将来の
`EODHDPaidProvider`や`TwelveDataProvider`も同じ境界へ追加できます。

将来のStreamlitは、DBをread-onlyで読むquery/service層を経由します。UIからProvider API、
学習処理、raw ingestionを直接実行しない構成にします。

## 9. Provider選択の仕組み

routerを通る日本株・海外市場価格のEOD系列は次の順序で評価されます。Treasuryは公式
専用経路で取得するため、この`provider_attempts` / `provider_selections`監査の対象外です。

1. Configに記載した優先順位で候補を取得する
2. Provider応答とOHLCを検証する
3. `data_quality`が許容対象か確認する
4. `available_timestamp <= cutoff`を確認する
5. 運用runでは`first_observed_at`と`retrieved_at`もcutoff以前か確認する
6. 必要な市場sessionを単一Providerで100% coverできるか確認する
7. 最初に全条件を通過したProviderだけを採用する
8. 全候補の結果と失敗理由をDBへ保存する

Yahooに1日だけ欠損がある場合、その1日だけEODHDで穴埋めすることはありません。
EODHD単独で必要期間をcoverできた場合に、系列全体をfallbackへ切り替えます。

## 10. 時刻と08:30判定

### 10.1 保存する時刻

| 時刻 | 意味 |
|---|---|
| `market_timestamp` | 市場イベント、bar、quoteの時刻 |
| `source_timestamp` | Providerが返した時刻。ない場合はNULL |
| `available_timestamp` | PIT選択に使う利用可能時刻 |
| `first_observed_at` | このシステムが値・revisionを初めて観測した時刻 |
| `retrieved_at` | HTTP等の取得が完了した時刻 |
| `last_seen_at` | 同じraw値を最後に再確認した時刻 |

基本関係は次のとおりです。

```text
market_timestamp
    <= available_timestamp
    <= first_observed_at
    <= retrieved_at
    <= last_seen_at
```

さらに次を満たします。

```text
source_timestamp IS NULL OR source_timestamp <= retrieved_at
```

### 10.2 予測cutoff

```text
cutoff_at = prediction_date 08:30 Asia/Tokyo
```

例:

```text
2026-08-10 08:30 JST = 2026-08-09 23:30 UTC
```

ジョブが08:35に開始してもcutoffを08:35へ変更しません。

### 10.3 EOD availability

```text
event_at     = 市場日付 + 公式close時刻
estimated_at = event_at + Provider lag
```

履歴APIが当時の配信時刻を返さない場合、`estimated_at`を
`PROVIDER_SLA_ESTIMATE`として保存します。ただし運用当日の利用では、
`first_observed_at <= cutoff`と`retrieved_at <= cutoff`も必須です。

実装上の基本式:

```text
available_timestamp = min(first_observed_at, estimated_at)
```

後日、同じ市場時刻にraw hashの異なる訂正版を初めて観測した場合は、そのrevisionの
`available_timestamp = first_observed_at`とし、元の日付へ遡及させません。

### 10.4 snapshot freshness

```text
snapshot_age = cutoff_at - market_timestamp
```

採用条件:

```text
available_timestamp <= cutoff_at
first_observed_at    <= cutoff_at
retrieved_at         <= cutoff_at
market_timestamp     <= cutoff_at
snapshot_age         <= sourceごとのmax_age
```

現在の上限:

| 種別 | max age |
|---|---:|
| FX | 10分 |
| 主な先物・Dollar Index | 20分 |
| 商品先物 | 40分 |

無料データのリアルタイム性は保証せず、実際のtimestampで毎回判定します。
snapshotでは`available_timestamp`を実際の取得・初回観測時刻とします。
`config/settings.yaml`のglobalな15分値は現行取得処理では使わず、各indicatorに設定した
10/20/40分を使用します。

## 11. 品質・鮮度・選択状態

### 11.1 DataQuality

| 値 | 意味 |
|---|---|
| `OFFICIAL` | U.S. Treasury等の公的発行元データ |
| `EOD_CONFIRMED` | EODとしてOHLC検証済みのデータ |
| `FREE_UNVERIFIED` | 無料・非公式で独立保証のないデータ |
| `DELAYED` | 遅延または遅延保証不明のsnapshot |
| `MISSING` | 使用可能な観測がない。偽の価格行は保存しない |

### 11.2 FreshnessStatus

| 値 | 意味 |
|---|---|
| `FRESH` | cutoffとmax ageを満たす |
| `STALE` | 古すぎる、または必要期間不足 |
| `AFTER_CUTOFF` | 08:30より後に利用可能・取得完了 |
| `FUTURE_TIMESTAMP` | Provider時刻がcutoffより未来 |
| `QUALITY_REJECTED` | 許容しない品質 |
| `MISSING` | Providerが値を返さない |

### 11.3 SelectionRole

| 値 | 意味 |
|---|---|
| `PRIMARY` | 優先Providerを採用 |
| `FALLBACK` | Primary不合格後に代替Providerを採用 |
| `NONE` | 採用可能なProviderなし |

品質が`OFFICIAL`でも古ければ使いません。`DELAYED`でも設定したmax age内で、すべての
PIT条件を満たせばsnapshot候補になります。

## 12. 現在実装済みの計算

### 12.1 米国債利回りspread

```text
10Y−2Y spread(t) = 10Y yield(t) - 2Y yield(t)
```

同じTreasury観測日の2Yと10Yが両方ある場合だけ生成します。

### 12.2 米国債利回り変化

```text
N観測日変化(t) = yield(t) - yield(t-N)
N = 1, 3, 5
```

ここで`N`はカレンダー日ではなく、Treasuryの観測行数です。金曜から月曜に1行ずつ
公表されていれば、月曜の1日変化は月曜値−金曜値です。

### 12.3 派生値のPIT

複数入力から作る値は、最も遅い入力より前には利用できません。

```text
derived.available_timestamp = max(input.available_timestamp)
derived.first_observed_at    = max(input.first_observed_at)
derived.retrieved_at         = max(input.retrieved_at)
```

派生rowは`provider=internal`とし、入力中で最も低い`data_quality`を継承します。入力の
どれかがdelayedなら派生rowもdelayedとして扱います。元のTreasury系列は15:30 ETを
market event、通常18:00 ETをschedule estimateとし、実測の公表保証とは区別します。

## 13. Phase 2で実装する計算方法

以下は元仕様で決まっている計算方針ですが、現時点ではまだコード・DB・画面へ実装されて
いません。

### 13.1 目的変数

```text
intraday_return = Close / Open - 1
price_difference = Close - Open
```

当日の寄り付きで買い、15:30終値で全売却し、翌日へ持ち越さない前提です。

### 13.2 価格特徴量

予定している代表式:

```text
1日return       = P(t) / P(t-1) - 1
N日累積return   = P(t) / P(t-N) - 1
log return      = log(P(t) / P(t-1))
MA20乖離率      = P(t) / mean(P(t-19:t)) - 1
```

1/2/3/5/20日return、5/20日volatility、open-close return、high-low rangeを作る予定です。
volatilityの自由度、high-low rangeの分母、Adjusted Closeの適用範囲はPhase 2の実装時に
テストとともに確定します。

### 13.3 学習

- 予測日より前の直近120 JPX営業日だけを使用
- `StandardScaler`は各training window内だけでfit
- RidgeをPrimaryとする
- `TimeSeriesSplit`でalpha等を調整
- random train/test splitは禁止
- 予測対象日や未来の実績をScaler、特徴量選択、学習へ入れない

Ridgeの基本形:

```text
minimize: Σ(y - Xβ)² + αΣβ²
```

### 13.4 BUY判定

初期仕様:

```text
Predicted Intraday Return > 0.30%
AND
Probability Up >= 60%
```

ただし、必要特徴量の欠損が許容範囲を超えた場合は
`Prediction Status = INSUFFICIENT_DATA`とし、BUYを出しません。

### 13.5 Walk Forward

```text
各予測日dについて:
  dより前の直近120営業日で学習
  dの1日だけを予測
  dの実績Open/Closeと比較
  次の営業日へ1日進める
```

評価値はOut-of-Sample結果だけから計算します。

### 13.6 売買損益

予定している基本計算:

```text
shares       = floor(capital_per_stock / Open)
gross_profit = (Close - Open) × shares
net_profit   = gross_profit - commission - slippage
```

日本株の100株単元を適用するか、手数料・スリッページを円固定またはbpsにするかは未確定
です。数値が確定するまでは、コストを暗黙に0として収益表示しません。

### 13.7 バックテスト評価値

予定式:

```text
Win Rate      = Wins / Number of Trades
Payoff Ratio  = Average Win / abs(Average Loss)
Profit Factor = Gross Profit / abs(Gross Loss)
Loss Rate     = Losses / Number of Trades
Expectancy    = Win Rate × Average Win
                - Loss Rate × abs(Average Loss)
```

`Gross Loss = 0`、取引0件、標準偏差0などの境界では、無限大やNaNをそのままランキングへ
使わず、明示的な状態を返す必要があります。

```text
Prediction Pearson Correlation
    = corr(predicted_return, actual_intraday_return)

Direction Accuracy
    = mean(sign(predicted_return) == sign(actual_intraday_return))
```

Spearman correlation、Sharpe、Sortino、Maximum DrawdownもOOSの日次取引結果から計算する
予定です。年率換算係数、無取引日の扱い、risk-free rateはPhase 2/3で確定します。

### 13.8 Readabilityと係数安定性

元仕様の重み:

```text
Readability Score
  = 35% × Profit Factor Score
  + 25% × Win Rate Score
  + 20% × Prediction Correlation Score
  + 10% × Direction Accuracy Score
  + 10% × Coefficient Stability Score
```

各要素を0〜100へ変換する正規化関数と上限clipは未確定です。20 trades未満は
`LOW SAMPLE`とし、Sample Penaltyを掛ける予定です。正規化式が確定するまでは
Readabilityの数値を表示しません。

直近20回のrolling trainingについて、特徴量ごとに次を保存・表示する予定です。

```text
mean_coefficient = mean(standardized coefficients)
coefficient_std  = std(standardized coefficients)
sign_stability   = 最頻符号と一致した回数 / 有効なtraining回数
```

係数安定性は予測精度や因果関係そのものではなく、model感応度がどの程度一貫しているかを
示す補助指標です。

## 14. ダッシュボードの見方（Phase 4予定仕様）

現在の7テーブルだけで将来まず表示できるのは、run/batch状況、raw値・revision、品質、
cutoff適格性、Yahoo/EODHDのattempt/selection、Treasury raw/derivedの健全性です。
予測、BUY、損益、バックテスト画面にはPhase 2以降のpipelineと追加テーブルが必要です。

### 14.1 最初に確認する共通項目

どのページでも、予測値より先に次を確認します。

1. `Data Cutoff`が対象日08:30 JSTか
2. `Generated At`とcutoffが混同されていないか
3. `Prediction Status`が`SUCCESS`か
4. stale、missing、fallback、proxyの警告がないか
5. 学習期間が対象日より前の120 JPX営業日か

`INSUFFICIENT_DATA`、`STALE`、`MISSING`がある銘柄は、予測値が表示されてもBUY対象に
しません。

### 14.2 Today

| 項目 | 見方 |
|---|---|
| Rank | 予測return等による順位。順位だけで売買判断しない |
| Predicted Return | 寄り付きから15:30までの予測騰落率 |
| Probability Up | `Close > Open`となる分類確率 |
| BUY / NO BUY | 設定したreturn・確率閾値の結果 |
| Readability | OOS結果から見た海外要因による読み取りやすさ |
| Profit Factor | OOS BUY取引のGross Profit / abs(Gross Loss) |
| Win Rate | OOS BUY取引の勝ち数 / 全取引数 |
| Expectancy | BUY 1回あたりの平均期待損益 |
| Confidence | 定義確定後の補助指標。確実性の保証ではない |
| Positive / Negative Factors | 標準化係数等から見た寄与候補 |

読み方の推奨順序:

1. Provider・欠損・cutoff状態
2. Predicted ReturnとProbability Up
3. OOSの取引件数とLOW SAMPLE警告
4. Profit Factor、Expectancy、最大Drawdown
5. 係数とその安定性

### 14.3 Stock Detail

- 当日予測とその基準価格
- 過去予測と実績のずれ
- OOS累積損益
- 取引件数、勝率、Profit Factor、Expectancy
- Pearson / Spearman correlation
- 係数の平均、標準偏差、符号一致率
- Provider変更、fallback、欠損履歴

予測と実績が近い数日だけでなく、全OOS期間、相場局面、最大損失を確認します。

### 14.4 Factor Analysis

標準化係数のヒートマップを表示する予定です。

- 正の係数: 指標上昇時に予測returnが上がる方向
- 負の係数: 指標上昇時に予測returnが下がる方向
- 絶対値: 同じmodel・同じ標準化条件内での相対的重要度

係数は因果関係を証明しません。係数が日ごとに大きく変わる場合は、読み取りやすさが低い
可能性があります。

### 14.5 Sector Analysis

業種内平均係数を比較します。銘柄数、学習期間、使ったProviderが同じか確認してから
比較します。

### 14.6 Backtest

確認する順序:

1. 期間とWalk Forward方式
2. 手数料・スリッページ
3. BUY threshold、Probability threshold、Top N
4. 取引件数とLOW SAMPLE
5. Net Profit、Expectancy、Profit Factor
6. Maximum Drawdown、Sharpe、Sortino
7. 個別Trade History

パラメータを過去結果に合わせすぎた場合、OOS表示でもselection biasが生じるため、設定変更
履歴を残す必要があります。

### 14.7 System Status

| 表示 | 正常の目安 |
|---|---|
| API connection | Yahoo / Treasuryが接続可能。EODHDは未設定でも可 |
| Last data fetch | 当日runの08:30 cutoff以前 |
| Last successful model run | 当日かつ対象営業日 |
| Database | 接続成功、migration head |
| Failed symbols | 0が理想。存在時は理由を表示 |
| Missing / Stale | 0が理想。存在時はBUY抑止 |
| Provider fallback | 使用時は`DEGRADED`等で明示 |

## 15. 将来のダッシュボード確認手順

Phase 4実装後は次の順序で確認します。現時点ではまだ実行しません。

```bash
test -f app.py && echo IMPLEMENTED || echo NOT_IMPLEMENTED
```

`IMPLEMENTED`となった後:

```bash
streamlit run app.py
```

ブラウザで表示したら、最低限次を検査します。

- DBに保存した最新runと画面のrun IDが一致する
- DBの`cutoff_at`と画面のData Cutoffが一致する
- Threshold変更がDBのcanonicalなBUY結果を書き換えない
- `INSUFFICIENT_DATA`銘柄がBUYにならない
- fallback、proxy、stale、missingが隠れない
- iPhone幅でも表や警告を読める
- 数値の丸め前後でBUY境界判定が変わらない

## 16. テストファイルから仕組みを確認する

| 確認内容 | テスト |
|---|---|
| Config・symbol・鮮度設定 | `tests/test_config.py` |
| Yahoo正規化・snapshot時刻 | `tests/test_yahoo_provider.py` |
| Treasuryと派生計算 | `tests/test_treasury_provider.py` |
| EODHD Free制限・秘密値 | `tests/test_eodhd_provider.py` |
| Provider fallback・混在防止 | `tests/test_provider_router.py` |
| cutoff・DST・as-of | `tests/test_market_alignment.py` |
| 未来情報の除外 | `tests/test_lookahead.py` |
| JPX休日・15:30化 | `tests/test_market_calendar.py` |
| revision・DB冪等性 | `tests/test_database.py` |
| CLI計画・EOD比較 | `tests/test_fetch.py` |

特定領域だけ確認する例:

```bash
pytest tests/test_provider_router.py -q
pytest tests/test_lookahead.py -q
pytest tests/test_treasury_provider.py -q
```

## 17. よくある状態と対処

### `DATABASE_URL is required`

`.env`の`DATABASE_URL`を設定し、PostgreSQLが起動しているか確認します。

### `EODHD_API_KEY is not configured`

`verify-eodhd`または`compare-eod`だけに必要です。通常のYahoo + Treasury運用では
未設定で構いません。

### snapshotがすべて`AFTER_CUTOFF`

08:30 JSTより後に取得した可能性があります。これは意図したlook-ahead防止です。
過去の08:30へ取得時刻を戻してはいけません。

### `STALE`

休日、市場休場、配信遅延、symbol停止、Yahoo障害を確認します。未来値や後続日の値で
補完しません。

### `provider does not cover the complete required window`

必要sessionの一部が欠けています。期間、上場日、取引停止、Provider応答を確認します。
Primaryの欠損日だけをfallbackで埋めることはありません。

### `PARTIAL`

`failed_sources`、`skipped_sources`、`unresolved_required`を確認します。現状はIron Ore未解決、
snapshot未要求、鮮度不合格などで発生し得ます。

## 18. 現在の合格基準

Phase 1を正常と判断する最低条件:

- `config-check`が`OK`
- pytestが77件成功
- ruff / mypyが成功
- Alembicがheadまで適用済み
- `market_timestamp <= available_timestamp <= first_observed_at <= retrieved_at`
- 08:30より後のsnapshotが採用されない
- 1系列内でYahooとEODHDが混在しない
- Provider試行と選択理由をDBで追跡できる
- 欠損を0や未来値で補完していない

Phase 2へ進む前に、Iron Oreを初期モデルから除外するか、欠損許容率、最低学習行数、
手数料・スリッページ、Yahooデータの公開範囲を決める必要があります。

## 19. 関連文書

- [README](../README.md)
- [無料データソースのカバレッジ](data-source-coverage.md)
- [Phase 1完了報告](phase1-report.md)
- [実装継続前の確認事項](decisions-needed.md)

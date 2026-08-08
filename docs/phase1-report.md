# Phase 1 完了報告

更新日: 2026-08-08

## 実装内容

- Python 3.12指定、依存関係、`.env.example`、Docker PostgreSQL設定
- 日本株22銘柄と37指標のstrict YAML設定
- Yahoo primary / U.S. Treasury official / EODHD Free optional fallback構成
- `YahooFinanceProvider`
  - 日本株 `.T`、海外指数・ETF・ADRの日足
  - FX、指数・商品先物の1分snapshot
  - `auto_adjust=False`によるraw OHLC / Adjusted Close分離
  - 非公式・無料データを`FREE_UNVERIFIED` / `DELAYED`として記録
- `TreasuryProvider`
  - 共通`MarketDataProvider` contractを実装
  - U.S. Treasury公式XMLから2Y / 10Y / 30Yを名前で抽出
  - 未知・欠落tenor、順不同、年跨ぎに対応
  - 10Y−2Yを同一観測日だけで派生し、入力の遅いavailabilityを継承
- `EODHDFreeProvider`
  - 任意の`EODHD_API_KEY`でのみ有効
  - EOD履歴約1年だけをfallback候補とし、Live / Treasuryを拒否
  - 外部仕様20 calls/dayに対してアプリ側5 calls/runを強制
- Provider router
  - `Mapping[str, MarketDataProvider]` registryから候補を解決
  - Yahooを先、EODHDを後とする決定的優先順位
  - 日本株もrouterを通し、候補試行と採用Providerを監査
  - 品質、PIT、必要session coverageを通過した系列だけを採用
  - 同一系列をProvider間で行単位に穴埋めしない
- 08:30 snapshot gate
  - immutable cutoff、最大age、future timestamp、取得完了時刻を検査
- point-in-time schema / alignment
  - market / source / availability / first-observed / retrieved / last-seen時刻を分離
  - `DataQuality`、`FreshnessStatus`、`SelectionRole`を分離
  - Provider混在を選択なしではfail closed
  - 訂正版を初回観測時刻より前へ遡及させない
- PostgreSQL / SQLAlchemy / Alembic
  - `0001_phase1_schema.py`
  - `0002_free_provider_quality.py`
  - `provider_attempts` / `provider_selections`によるfallback監査
- `data.fetch` CLI
  - `config-check`
  - `verify-yahoo`
  - `verify-eodhd`
  - `compare-eod`
  - `fetch-free`

## データカバレッジ

`python -m data.fetch config-check`の現在値:

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

Baltic Dry IndexはBDRY ETF proxyです。直接BDI、Capesize、Panamaxは未設定です。
詳細は[データソース確認](data-source-coverage.md)を参照してください。

## 検証結果

現在のローカル結果:

```text
python -m pytest -q        77 passed
```

77件には、Yahoo / Treasury / EODHD Freeのnormalizationと障害処理、無料枠、Provider
fallback、系列混在防止、snapshot鮮度、cutoff境界、revision PIT、DST、JPX休日、設定、
repository / migration modelのテストを含みます。直近の追加分は、operational EOD取得の
`retrieved_at` cutoff、operational alignmentの`first_observed_at` cutoff、Treasuryの
共通Provider contract、および`compare-eod`が指数とETF proxyを誤比較しないことを
検証します。JPXの2024-11-05以降の15:30終値もcalendar回帰テストで固定しています。

プロジェクトはPython 3.12以上を要求しますが、この端末の`.venv`はPython 3.11.3です。
したがってPython 3.12環境での再実行は残課題です。またSQLite単体テストはありますが、
実PostgreSQL serverでのmigration / upsert integration testはまだ実施していません。

実Providerのsmoke確認では、日本株22個の`.T` symbolと主要なYahoo symbolの応答を確認し、
`NIY=F`、`ES=F`、`NQ=F`、`CL=F`、`JPY=X`のsnapshotで市場timestampと取得timestampを
別々に取得できました。休日の古い行は`DELAYED`として正規化され、鮮度gateの対象です。
U.S. Treasury実XMLからは2026年分453 tenor行を正規化できました。現在の環境には
`EODHD_API_KEY`がないため、EODHD Freeの実account-level確認は未実施です。

## 動作確認

設定だけを検証:

```bash
python -m data.fetch config-check
```

Yahoo symbolをbest-effort確認:

```bash
python -m data.fetch verify-yahoo
```

任意のEODHD Free keyを設定した場合だけfallback symbolを確認:

```bash
python -m data.fetch verify-eodhd
```

`EODHD_API_KEY`を使い、同じ上場商品のYahoo / EODHD Free終値を設定上限5系列以内で比較:

```bash
python -m data.fetch compare-eod \
  --from-date YYYY-MM-DD \
  --to-date YYYY-MM-DD \
  --max-series 5
```

DB作成と履歴取得:

```bash
alembic upgrade head
python -m data.fetch fetch-free \
  --from-date YYYY-MM-DD \
  --to-date YYYY-MM-DD
```

08:30 snapshot / operational PIT gateも実行:

```bash
python -m data.fetch fetch-free \
  --from-date YYYY-MM-DD \
  --to-date YYYY-MM-DD \
  --prediction-date YYYY-MM-DD \
  --include-snapshots
```

取得CLIには`DATABASE_URL`が必要です。`EODHD_API_KEY`は任意です。`verify-yahoo`と
`fetch-free`はYahooへのネットワークアクセス、Treasury取得は
[公式XML feed](https://home.treasury.gov/treasury-daily-interest-rate-xml-feed)へのアクセスを
必要とします。

## 既知の制約

- Yahoo / `yfinance`は非公式・best-effort・personal research向けでSLAがない
- Yahoo intraday履歴には期間制限があり、本実装の1分足は直近snapshot専用
- EODHD Freeは20 calls/day、過去1年EODのみ。本アプリは5 calls/run
- Treasuryは公式値だが、XMLに各履歴行の公表timestampがない
- 歴史バックフィルの真の配信時刻を復元できないため、厳格PITと推定PITを区別する
- Iron Oreは必須だが未解決
- BDRYはBDIの代理で、直接BDI / Capesize / Panamaxではない
- 公開Dashboard、メール、raw / derivedデータ再配布の許諾は未確認

## 残課題

- Python 3.12と実PostgreSQLでmigration / integration test
- Iron Oreと直接Baltic系列のlicensed PIT source
- 公開・メール配信時のYahoo / EODHD利用許諾
- 08:29再取得、15:45以後の実績確定、scheduler SLA
- Phase 2: 特徴量、Ridge、walk-forward、backtest
- Phase 3以降: 評価指標、Streamlit、Email / Scheduler、追加モデル

Phase 1だけでは予測、BUY判定、収益表示を行いません。

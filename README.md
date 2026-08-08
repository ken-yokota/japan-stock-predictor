# Japan Stock Predictor

日本時間08:30までに利用可能だった情報だけを使い、日本株の「寄り付き買い・
15:30大引け売り」を分析するWebアプリのリポジトリです。

現在は **Phase 1** までの実装です。無料データProvider、設定検証、PostgreSQL /
Alembic、08:30の鮮度判定、point-in-time（PIT）alignment、取得監査が対象です。
予測モデル、バックテスト、Streamlit、メール、定期実行はPhase 2以降であり、まだ
投資判断に使用できる完成版ではありません。

> このソフトウェアは個人の投資分析・研究用です。価格上昇や利益を保証せず、売買を
> 自動発注しません。データの鮮度、欠損、利用条件を確認し、最終判断は利用者自身で
> 行ってください。

## 無料Provider構成

| 役割 | Provider | 現在の用途 | 重要な制約 |
|---|---|---|---|
| Primary | Yahoo Finance / `yfinance` | 日本株22銘柄、海外EOD、08:30 snapshot | 非公式、best-effort、個人の研究・教育用途。配信時刻・正確性・継続提供のSLAなし |
| Official | U.S. Treasury | 2Y、10Y、30YのDaily Treasury Par Yield Curveと10Y−2Y派生値 | 値は公式だが、XMLは各履歴行の公表時刻を返さないため08:30可否は別途検査 |
| Optional fallback | EODHD Free | Yahooの海外EOD系列が品質・PIT・coverage gateを通らない場合だけ使用 | API key任意、EODのみ、過去約1年、公式上20 calls/day、アプリ側は5 calls/run |

`yfinance`自身も、Yahooとは非提携・非承認のOSSで、取得データはpersonal use向け、
research / educational purposes向けだと明記しています。公開Dashboard、第三者向け
メール、業務利用、データ再配布を行う前にYahooの利用条件と各データ提供者の許諾を
確認してください。[yfinance README](https://github.com/ranaroussi/yfinance#readme)

EODHD FreeはPrimaryではありません。公式Quick Startでは、無料プランは
[20 API calls/day、EOD履歴は過去1年まで](https://eodhd.com/financial-apis/quick-start-with-our-financial-data-apis)
です。本アプリは誤消費を避けるため `config/settings.yaml` で1実行5 callsに制限し、
LiveとTreasury endpointを無効化しています。

全22銘柄・全37指標の現在のsource、symbol、08:30可否、fallbackは
[データソース確認](docs/data-source-coverage.md)にあります。

実行確認、DB確認SQL、アーキテクチャ、計算方法、将来のダッシュボードの読み方は
[確認・ダッシュボード・アーキテクチャガイド](docs/verification-and-dashboard-guide.md)を
参照してください。

## Phase 1で実装済み

- `config/stocks.yaml`: 日本株22銘柄と明示的なYahoo `.T` symbol
- `config/indicators.yaml`: 37指標のYahoo primary、Treasury official、EODHD Free fallback
- Pydantic v2によるstrict YAML検証、重複・timezone・source metadata検査
- 共通`MarketDataProvider` interfaceを実装する`YahooFinanceProvider`、
  `TreasuryProvider`、`EODHDFreeProvider`
- timeout、429 / 5xx / transport retry、指数backoff、秘密値を含まない例外
- raw OHLCとAdjusted Closeの分離。Yahoo取得では`auto_adjust=False`を明示
- `market_timestamp`、`source_timestamp`、`available_timestamp`、
  `first_observed_at`、`retrieved_at`、`last_seen_at`の分離
- `DataQuality`、snapshot鮮度、primary / fallback選択を別々に記録
- 08:30固定cutoffを超える値、未来timestamp、許容時間を超えたsnapshotの除外
- 日本株を含む市場系列ごとのProvider routingと選択監査。YahooとEODHDの行を
  穴埋め結合しない混在防止
- 後日訂正版を初回観測時刻より前へ遡及させないrevision保持
- PostgreSQL用SQLAlchemy 2 models、冪等保存、2本のAlembic migration
- Provider候補の全試行と採用Providerを`provider_attempts` / `provider_selections`へ保存
- Provider、fallback、PIT、DST、休日、設定、DBを対象とする77件のpytest

## 08:30鮮度とPIT

予測cutoffは対象JPX営業日の `08:30 Asia/Tokyo` に固定し、ジョブ実行時刻から独立させ
ます。運用取得では、少なくとも次をすべて満たした値だけを採用します。

- `market_timestamp <= available_timestamp <= cutoff_at`
- `first_observed_at <= cutoff_at` かつ `retrieved_at <= cutoff_at`
- EODは対象市場カレンダー上の必要windowを単一Providerで完全にカバー
- snapshotはsource別の最大age以内。FXは10分、主な先物は20分、商品先物は40分
- 設定された品質を満たすこと。`OFFICIAL`でも古ければ除外する

EOD fallbackは候補ごとにwindow全体を評価し、通過した1 Providerだけを選びます。
Yahooの不足日だけをEODHDで埋める処理は行いません。後から訂正版が届いた場合も、
過去cutoffのwalk-forward入力へ遡及させません。

Yahooの1分足は直近snapshot取得専用です。`yfinance`のhistory実装ではintraday履歴に
期間制限があり、長期の1分足バックテスト用データ源としては扱いません。
[yfinance history実装](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py)

## 構成

```text
config/                 銘柄・指標・運用設定
data/
  providers/            Yahoo、Treasury、EODHD Free、Provider interface
  provider_router.py    品質・PIT・coverage gateと単一Provider選択
  snapshot.py           08:30 snapshot鮮度判定
  availability.py       利用可能時刻の導出
  alignment.py          cutoff以前のas-of選択とProvider混在防止
  market_calendar.py    JPX営業日
  fetch.py              設定確認・symbol確認・無料Provider取得CLI
database/               SQLAlchemy models、connection、repository
alembic/                Phase 1 schemaと無料Provider監査migration
docs/                   データカバレッジ、未決事項、Phase報告
tests/                  Phase 1 tests
```

## セットアップ

必要条件はPython 3.12以上とPostgreSQLです。

```bash
cd /Users/yokotaken/Desktop/japan-stock-predictor
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

`.env`へ実値を設定します。YahooとTreasuryにAPI keyは不要です。
`EODHD_API_KEY`はfallbackを有効にするときだけ設定します。

```dotenv
EODHD_API_KEY=
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME
RESEND_API_KEY=
EMAIL_FROM=
EMAIL_TO=
APP_URL=http://localhost:8501
TIMEZONE=Asia/Tokyo
```

ローカルPostgreSQLをDockerで起動する場合:

```bash
docker compose up -d postgres
alembic upgrade head
```

Render等の`postgres://` / `postgresql://` URLはpsycopg 3形式へ正規化します。
SQLiteは単体テスト用であり、本番CLIでは拒否します。実PostgreSQLでのmigration統合
確認は未完了です。

## CLI

### 設定検証

ネットワーク、DB、API keyなしでYAMLを検証できます。

```bash
python -m data.fetch config-check
```

現在の期待値は日本株22、EOD指標17、snapshot指標12、Treasury tenor 3、未解決の必須
指標 `iron_ore` です。

### Symbol確認

Yahooは公式symbol catalogを提供しないため、価格取得によるbest-effort確認です。

```bash
python -m data.fetch verify-yahoo
```

任意のEODHD Free keyを設定した場合だけfallback symbolを確認できます。このコマンドも
無料枠を消費します。

```bash
python -m data.fetch verify-eodhd
```

`EODHD_API_KEY`を設定済みの場合、同じ上場商品だけを対象にYahoo / EODHD Freeの
終値を比較できます。指数とETF proxyのように比較不能な組合せは除外し、
`--max-series`は設定済みの1実行上限5以下にします。

```bash
python -m data.fetch compare-eod \
  --from-date 2026-08-01 \
  --to-date 2026-08-07 \
  --max-series 5
```

### 無料Providerから取得

DB migration後に実行します。

```bash
alembic upgrade head
python -m data.fetch fetch-free \
  --from-date 2026-01-01 \
  --to-date 2026-08-07
```

08:30 snapshotと運用時PIT gateを有効にする場合:

```bash
python -m data.fetch fetch-free \
  --from-date 2026-01-01 \
  --to-date 2026-08-07 \
  --prediction-date 2026-08-10 \
  --include-snapshots
```

`--include-snapshots`では、履歴行を含め取得完了が08:30を過ぎた値を運用入力から除外
します。ジョブが遅れた場合もcutoffは現在時刻へ動きません。

## 品質確認

```bash
python -m pytest -q
ruff check .
mypy data database
alembic check
```

現時点のpytestは `77 passed` です。詳細は[Phase 1報告](docs/phase1-report.md)を参照して
ください。

GitHubへpushした後は[CI workflow](.github/workflows/ci.yml)がPython 3.12で同じpytest、
ruff、mypy、Alembic確認を自動実行します。朝予測やclose更新のscheduled workflowは
Phase 5で追加します。

## 既知の未解決事項

- 必須のIron Oreに、利用許諾とPIT時刻を確認できる無料sourceがない
- Baltic Dry Indexは現在BDRY ETFによる代理であり、直接BDIではない
- Baltic Capesize / Panamaxの直接系列は未設定
- Yahoo / EODHDデータを公開Dashboardや第三者メールで再配布できるか未確認
- 実PostgreSQLでmigrationとupsertの統合検証が未実施
- Phase 2以降の特徴量、モデル、walk-forward、Dashboard、Email、Schedulerは未実装

残る判断事項は[確認事項](docs/decisions-needed.md)を参照してください。

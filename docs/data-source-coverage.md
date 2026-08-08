# 無料データソースのカバレッジ

確認日: 2026-08-08
対象: 現在の `config/*.yaml` と `data.fetch` の実装

## 結論

現在の取得経路は、Yahoo Financeを市場価格のprimary、米国財務省XMLを米国債利回りのprimary、EODHD Freeを任意のEOD fallbackとする。EODHDの有料プランは前提にしない。

`python -m data.fetch config-check` が示す現在の計画は次のとおり。

- 日本株: 22銘柄
- Yahoo EOD指標: 17系列
- Yahoo 08:30 snapshot指標: 12系列
- U.S. Treasury: 2Y、10Y、30Yの3系列
- 未解決の必須指標: `iron_ore`

したがって、無料経路だけでアプリの取得基盤は動作するが、仕様上の全特徴量を満たした状態ではない。Iron Oreを欠損のまま明示し、偽値や無関係な代理値で補完しない。

## Provider方針

| Provider | 現在の役割 | 認証 | 保存する品質 | 主な制約 |
|---|---|---|---|---|
| Yahoo Finance (`yfinance`) | 日本株、海外EOD、08:30 snapshotのprimary | 不要 | EOD=`FREE_UNVERIFIED`、snapshot=`DELAYED` | 非公式・best effort。個人の調査・教育目的を前提とし、SLAや再配布権を保証しない |
| U.S. Treasury | 2Y、10Y、30Y利回りのprimary | 不要 | `OFFICIAL` | 公式XMLは各行の実配信時刻を持たないため、公開予定時刻を推定として扱う |
| EODHD Free | Yahoo EOD系列だけの任意fallback | `EODHD_API_KEY`（任意） | `EOD_CONFIRMED` | Freeは20 calls/day、過去約1年のEODに限定。アプリ側は5 calls/run。snapshot、Treasury、日本株のfallbackには使わない |

品質値は `OFFICIAL`、`EOD_CONFIRMED`、`FREE_UNVERIFIED`、`DELAYED`、`MISSING` の5種類であり、鮮度判定 (`FRESH` など) と選択役割 (`PRIMARY` / `FALLBACK`) とは別に保存する。

3つのconcrete Providerはいずれも共通`MarketDataProvider` contractを実装する。市場価格の実行経路は`Mapping[str, MarketDataProvider]`のregistryから候補を解決し、日本株も海外EODと同じrouter、PIT / coverage gate、`provider_attempts` / `provider_selections`監査を通る。Treasury固有のtenor取得も同じProvider contract上に実装している。

Yahoo経路は [`yfinance` の公式README](https://github.com/ranaroussi/yfinance#readme) が明記するようにYahooとの提携・承認を受けたものではなく、研究・教育および個人利用を意図したライブラリである。Web公開、複数利用者への表示、メール配信、データ再配布を行う前に、[Yahooの利用規約](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html)を含む権利確認が必要となる。

EODHD Freeの20 calls/dayと過去1年のEOD制限は[公式Quick Start](https://eodhd.com/financial-apis/quick-start-with-our-financial-data-apis)に基づく。利用・保存・再配布条件は[EODHD利用規約](https://eodhd.com/financial-apis/terms-conditions)を別途確認する。

## 日本株22銘柄

現在はすべてYahooの `.T` symbolを使い、EODHD symbolは意図的に `null` としている。EODHDの現行[対応価格取引所一覧](https://eodhd.com/list-of-stock-markets)に日本市場が掲載されておらず、`.TSE`などを推測して登録しないためである。市場は `XTKS`、timezoneは `Asia/Tokyo`。08:30 JSTでは、休日カレンダーで求めた直前の完了済みJPX sessionの日足だけが候補となり、共通routerのPIT・品質・必要期間のcoverage gateを通過した場合に利用する。候補試行と採用結果は海外EODと同様にDBへ記録する。

| セクター | 対象銘柄 | Yahoo symbol | EODHD Free fallback |
|---|---|---|---|
| 海運 | 9101 日本郵船、9104 商船三井、9107 川崎汽船 | `9101.T`, `9104.T`, `9107.T` | なし |
| 石油・エネルギー | 1605 INPEX、5020 ENEOS HD、5019 出光興産、5021 コスモエネルギーHD | `1605.T`, `5020.T`, `5019.T`, `5021.T` | なし |
| 自動車 | 7203 トヨタ、7267 ホンダ、7201 日産、7269 スズキ、7270 SUBARU | `7203.T`, `7267.T`, `7201.T`, `7269.T`, `7270.T` | なし |
| 金融 | 8306 三菱UFJ、8316 三井住友FG、8411 みずほFG、8604 野村HD、8766 東京海上HD | `8306.T`, `8316.T`, `8411.T`, `8604.T`, `8766.T` | なし |
| 商社 | 8001 伊藤忠、8002 丸紅、8031 三井物産、8053 住友商事、8058 三菱商事 | `8001.T`, `8002.T`, `8031.T`, `8053.T`, `8058.T` | なし |

`verify-yahoo` は価格取得によるbest-effort確認であり、取引所による公式symbol認証ではない。またYahooの日足バックフィルには当時の公開時刻が含まれないため、履歴のavailabilityは実装上の市場終了時刻＋保守的lagによる推定である。

## 海外指標の実装カバレッジ

「08:30可否」は無条件の保証ではない。EODは直前の必要sessionを含む全期間のcoverage、quality、PITを通過する必要があり、snapshotはさらに表の最大経過時間以内でなければならない。

| ID | Yahoo primary | モード / 08:30条件 | EODHD Free fallback | 状態・注意 |
|---|---|---|---|---|
| `sp500` | `^GSPC` | EOD / 条件付き可 | `SPY.US` EOD proxy | proxy採用時は指数そのものではない |
| `nasdaq100` | `^NDX` | EOD / 条件付き可 | `QQQ.US` EOD proxy | proxy採用時は指数そのものではない |
| `dow` | `^DJI` | EOD / 条件付き可 | `DIA.US` EOD proxy | proxy採用時は指数そのものではない |
| `vix` | `^VIX` | EOD / 条件付き可 | なし | Yahoo失敗時は欠損 |
| `usdjpy` | `JPY=X` | snapshot / 10分以内 | なし | `yfinance` 1分足の最新行 |
| `eurjpy` | `EURJPY=X` | snapshot / 10分以内 | なし | 同上 |
| `dollar_index` | `DX-Y.NYB` | snapshot / 20分以内 | 実行経路ではなし | `UUP.US`は設定にあるが、EOD proxyをsnapshot fallbackとして混在させない |
| `nikkei225_futures` | `NIY=F` | snapshot / 20分以内 | なし | continuous futures symbol |
| `sp500_futures` | `ES=F` | snapshot / 20分以内 | なし | continuous futures symbol |
| `nasdaq100_futures` | `NQ=F` | snapshot / 20分以内 | なし | continuous futures symbol |
| `gold` | `GC=F` | snapshot / 40分以内 | 実行経路ではなし | `GLD.US` EOD proxyはsnapshot代替にしない |
| `us_2y_yield` | U.S. Treasury `BC_2YEAR` | 日次 / PIT条件付き | なし | 公式値 |
| `us_10y_yield` | U.S. Treasury `BC_10YEAR` | 日次 / PIT条件付き | なし | 公式値 |
| `us_30y_yield` | U.S. Treasury `BC_30YEAR` | 日次 / PIT条件付き | なし | 公式値 |
| `us_10y_minus_2y_spread` | 内部計算 | 両入力が同日かつPIT-safe | なし | `10Y - 2Y`、利用可能時刻は遅い側を継承 |
| `baltic_dry_index` | `BDRY` | EOD / 条件付き可 | `BDRY.US` EOD proxy | BDRY ETF proxyであり、Baltic Dry Indexの直接値ではない |
| `baltic_capesize_index` | なし | optional / 不可 | なし | licensed PIT source未選定 |
| `baltic_panamax_index` | なし | optional / 不可 | なし | licensed PIT source未選定 |
| `fxi` | `FXI` | EOD / 条件付き可 | `FXI.US` EOD proxy | ETF |
| `mchi` | `MCHI` | EOD / 条件付き可 | `MCHI.US` EOD proxy | ETF |
| `copper` | `HG=F` | snapshot / 40分以内 | 実行経路ではなし | `CPER.US` EOD proxyはsnapshot代替にしない |
| `iron_ore` | なし | required / 不可 | なし | 現在唯一の未解決必須指標 |
| `wti` | `CL=F` | snapshot / 40分以内 | 実行経路ではなし | `USO.US` EOD proxyはsnapshot代替にしない |
| `brent` | `BZ=F` | snapshot / 40分以内 | 実行経路ではなし | `BNO.US` EOD proxyはsnapshot代替にしない |
| `audjpy` | `AUDJPY=X` | snapshot / 10分以内 | なし | `yfinance` 1分足の最新行 |
| `us_shipping_equity_proxy` | なし | optional / 不可 | なし | 採用proxy未決定 |
| `xle` | `XLE` | EOD / 条件付き可 | `XLE.US` EOD proxy | ETF |
| `oih` | `OIH` | EOD / 条件付き可 | `OIH.US` EOD proxy | ETF |
| `natural_gas` | `NG=F` | snapshot / 40分以内 | 実行経路ではなし | `UNG.US` EOD proxyはsnapshot代替にしない |
| `xli` | `XLI` | EOD / 条件付き可 | `XLI.US` EOD proxy | ETF |
| `ewy` | `EWY` | EOD / 条件付き可 | `EWY.US` EOD proxy | ETF |
| `toyota_adr` | `TM` | EOD / 条件付き可 | `TM.US` EOD proxy | 7203向け特徴量 |
| `honda_adr` | `HMC` | EOD / 条件付き可 | `HMC.US` EOD proxy | 7267向け特徴量 |
| `xlf` | `XLF` | EOD / 条件付き可 | `XLF.US` EOD proxy | ETF |
| `kre` | `KRE` | EOD / 条件付き可 | `KRE.US` EOD proxy | ETF |
| `mufg_adr` | `MUFG` | EOD / 条件付き可 | `MUFG.US` EOD proxy | 8306向け特徴量 |
| `smfg_adr` | `SMFG` | EOD / 条件付き可 | `SMFG.US` EOD proxy | 8316向け特徴量 |

現在の実行経路でEODHD Free候補を持つYahoo EOD系列は16本である。ただし、無料枠保護の5 calls/run制限があるため、同じrunで16本すべてを救済できるという意味ではない。EODHD API keyがなければYahooのみで実行し、fallbackは無効となる。

## 08:30 freshnessとpoint-in-timeルール

予測基準時刻は `prediction_date` の08:30 JSTで固定する。取得時刻を後から08:30へ丸めたり、08:30より後に到着した値を朝の特徴量へ戻したりしない。

保存する主要時刻は次の4つである。

1. `market_timestamp`: 市場イベントまたはquoteの時刻
2. `available_timestamp`: Providerの公開規則または初回観測から導出した利用可能時刻
3. `first_observed_at`: アプリがその値・版を初めて観測した時刻
4. `retrieved_at`: 実際に取得した時刻

snapshotは、4時刻の関係、データ品質、future timestamp、表に記載した最大ageを検査し、1つでも違反すれば `STALE`、`AFTER_CUTOFF`、`FUTURE_TIMESTAMP`、`QUALITY_REJECTED` または `MISSING` として除外する。

EOD routingも候補ごとに品質とPITを検査し、必要sessionを単独で完全に覆う最初のProviderだけを採用する。Yahooの一部の日だけをEODHDで穴埋めするrow-level mergeは禁止する。選ばれた系列の全rowは同じraw `provider` でなければならず、試行理由と最終選択をDBへ記録する。proxy fallbackを選んだ場合も、proxyであることを特徴量定義とprovenanceに残す。

実運用runでは `available_timestamp <= cutoff` に加え、`first_observed_at <= cutoff` と `retrieved_at <= cutoff` が必須である。履歴バックフィルは過去時点の実配信時刻を復元できないため、再現バックテストではavailability推定とrevision biasを別途監査する必要がある。

## 休日・欠損

- 日本株はJPX (`XTKS`)、米国EODはNYSE (`XNYS`) のsession calendarで必要日を決める。単純な平日joinはしない。
- 各市場の祝日や時差が異なるため、同じ暦日を強制的に揃えず、08:30までに利用可能な直近完了sessionを使う。
- 必須windowを完全にcoverできなければそのProvider系列を不採用にする。
- 未解決・失敗・古い値は `MISSING` または取得reportの失敗理由として残し、0埋めや将来値による補完をしない。
- `iron_ore` が未解決のため、必要特徴量の最大欠損率が未設定の現状では本番予測をfail closedにする設計判断が残る。

## Provider固有の注意

### Yahoo / yfinance

- Yahooとは独立した非公式ライブラリで、個人の研究・教育用途を前提とする。
- 市場ごとの公称遅延は[Yahoo Financeの取引所・遅延一覧](https://help.yahoo.com/kb/finance/article-exchanges-data-delays-sln2310.html)を参考にするが、アプリは毎回実timestampから独立に鮮度判定する。
- EODの当時の公開時刻は返らず、availabilityは市場終了時刻とlagからの推定を含む。
- 実装するsnapshotは `period="1d", interval="1m"` の最新行であり、長期履歴ではない。
- [`yfinance` のhistory実装](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py)はintraday履歴を直近60日以内に制限し、intervalによってさらに短い制限があり得る。過去の各日08:30 snapshotを長期に一括復元できる前提を置かない。
- symbol、遅延、欠損、仕様変更にSLAはない。毎runでtimestampと鮮度を検査する。

### U.S. Treasury

- [公式Daily Treasury Par Yield Curve XML](https://home.treasury.gov/treasury-daily-interest-rate-xml-feed)から2Y、10Y、30Yを取得する。
- feedの観測日は公式だが、各行の原配信timestampは含まれない。
- [Treasuryの公式methodology](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology)に基づき、実装は15:30 ETを市場イベント時刻、通常18:00 ETを公開予定時刻として保存し、`published_schedule_estimate` flagを付ける。これは実測の配信保証ではない。
- operational runでは、最新の米国sessionであることに加え、初回観測と取得が08:30 JST cutoff以前であることを要求する。

### EODHD Free

- API keyがある場合だけ有効になる任意fallbackであり、無料利用に有料endpointの権利を仮定しない。
- EOD only、過去約1年、公式20 calls/day。アプリはretryを含む過剰消費を抑えるため5 calls/runでfail closedにする。
- generic live、Treasury、日本株は現在のfallback対象外である。
- `verify-eodhd` は設定済みEOD fallback symbolをprovider catalogと照合するが、契約・配信・再配布権まで保証するものではない。
- `compare-eod` はEODHD key必須で、YahooとEODHD Freeで同じ上場商品を表すsymbolだけを設定上限の最大5系列比較する。指数対ETFなど比較不能なproxy組合せは除外する。

## 未解決事項

1. 必須のIron Oreについて、08:30以前のPIT証跡、履歴revision、継続利用条件を満たすsourceが未選定。
2. `BDRY` / `BDRY.US` はETF proxyであり、Baltic Dry Indexの直接値ではない。直接BDI、Capesize、Panamaxにはlicensed sourceが必要。
3. Yahoo、EODHDおよび派生結果を一般公開・複数利用者へ表示・メール配信できるか、public redistributionの法務・契約確認が未完了。
4. 先物symbolはcontinuous contractであり、roll調整・期限乗換え・当時の構成を含む再現性方針が未確定。

これらが解決するまで、アプリは個人研究用・best effortとして扱い、欠損やproxy利用を画面とrun reportへ明示する。

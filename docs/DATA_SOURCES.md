# データソース

更新日: 2026-08-08

## 無料Provider方針

| Provider | 用途 | 認証 | 品質label | 制約 |
|---|---|---|---|---|
| Yahoo Finance / `yfinance` | 日本株OHLC、米国株・ETF・ADR・指数・FX・先物・VIX | 不要 | 主に`FREE_UNVERIFIED`、snapshotは`DELAYED` | 非公式・best effort。取引所公式feedではなく、鮮度・正確性・継続提供・再配布のSLAなし |
| U.S. Department of the Treasury | Daily Treasury Par Yield Curve Ratesの2Y/10Y/30Y | 不要 | `OFFICIAL` | 値は公式だが、historical XML行に実際の公開時刻がないためavailabilityは推定を含む |
| EODHD Free | symbol検証、比較、Yahoo EODの一部fallback、将来移行準備 | `EODHD_API_KEY`（任意） | `EOD_CONFIRMED` | Primaryではない。20 calls/day・およそ1年のEOD制約を前提に、アプリは5 calls/runへ制限。日本株価格・live・Treasury fallbackには使用しない |

`provider`と`data_quality`は別概念である。さらに`freshness_status`と`selection_role`も独立して保存する。品質が高くても古い値は使わない。

## 対象日本株

`config/stocks.yaml`に22銘柄を定義する。全銘柄の主経路はYahooの`<code>.T`、市場は`XTKS`、timezoneは`Asia/Tokyo`である。セクターはshipping、oil_energy、automotive、financial、trading_company。EODHDの日本株symbolは未確認のため`null`であり、推測したsymbolで埋めない。

## 主要指標

全37指標の正確なsymbol、sector mapping、必須/任意、proxy情報は`config/indicators.yaml`と`docs/data-source-coverage.md`を正とする。主要な運用対象は次のとおり。

| 系列 | Yahoo symbol / source | Mode | 08:30条件 |
|---|---|---|---|
| S&P 500 / NASDAQ 100 / Dow / VIX | `^GSPC`, `^NDX`, `^DJI`, `^VIX` | previous EOD | 米国の直前完了sessionとcoverage gateを通ること |
| USDJPY / EURJPY / AUDJPY | `JPY=X`, `EURJPY=X`, `AUDJPY=X` | 1m snapshot | cutoff以前、最大10分age |
| Nikkei / S&P 500 / NASDAQ futures | `NIY=F`, `ES=F`, `NQ=F` | 1m snapshot | cutoff以前、最大20分age |
| WTI / Brent / Gold / Copper / Natural Gas | `CL=F`, `BZ=F`, `GC=F`, `HG=F`, `NG=F` | 1m snapshot | cutoff以前、最大40分age |
| U.S. 2Y / 10Y / 30Y | Treasury XML | daily | 公式値。利用可能時刻の推定とcutoff gateを通ること |
| 10Y−2Y、1/3/5観測日変化 | 内部派生 | daily | 全入力が同一PIT windowで利用可能なこと |
| BDI | Yahoo `BDRY` | EOD proxy | Baltic Dry Indexの直接値ではなくETF proxy |
| Iron Ore | 未設定 | missing | 現在は未解決。未来値や無関係なproxyで補完しない |

EODHD fallback symbolを持つ系列でも、proxyとdirect instrumentは同一価格系列とはみなさない。snapshotの代わりに前日ETF EODを混在させない。

## Timestampとtimezone

- `market_timestamp`: 市場イベント/quote時刻。
- `source_timestamp`: Providerが明示したsource時刻。存在しない場合はnull。
- `available_timestamp`: その値を利用可能とみなす最早時刻。
- `first_observed_at`: アプリがそのraw revisionを初めて観測した時刻。
- `retrieved_at`: HTTP/ライブラリ取得を完了した時刻。
- `last_seen_at`: 同じrevisionを最後に再確認した時刻。

保存はtimezone-aware、比較はUTC、業務表示はJSTとする。米国市場sessionは`America/New_York`でDSTを扱う。prediction cutoffは`prediction_date 08:30 Asia/Tokyo`固定。

## 採用条件

EODは候補Provider単独で必要session windowをcoverし、qualityとPIT gateを通過する必要がある。snapshotはさらにsource別のmax ageを満たす。失敗またはstaleなら当日の特徴量から除外し、後の値で補完しない。候補の全試行は`provider_attempts`、採用結果は`provider_selections`へ保存する。

## 利用条件上の注意

本構成が無料であることは、データを自由に公開・再配布できることを意味しない。yfinanceはYahoo非公認のOSSである。Streamlit公開範囲、メール受信者、保存期間、商用利用を広げる前にYahoo、取引所、EODHDその他各権利者の最新規約を利用者自身で確認する。

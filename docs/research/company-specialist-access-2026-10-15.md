# 専門指標の取得経路・追加契約要件

調査依頼日: 2026-10-15。最終確認: 2026-10-16。データ取得の可否を調べた資料であり、DB投入・運用開始・予測力検証の完了報告ではない。

## 結論

JEPXの公式年度CSVは匿名取得を実証した。JKM現物・AFEI・Baltic各運賃・RIM評価の必要な日次履歴は未確保。FBX/FAXの公式APIは確認できるが匿名アクセスは401。INPEXの7群は、特にJKM/AFEIが残るため **INSUFFICIENT_DATA**。月次LNG輸入価格や米国プロパンに交換して完備とはしない。

RBAのAUD/USD公式CSVは存在するが、この環境では403だった。これは公開データの有無と取得経路の可用性を区別するための記録である。

## 指標別の取得仕様

### lng_jkm — アジアLNG現物価格・Platts JKM

- 状態: `SUBSCRIPTION_REQUIRED_NO_HISTORY_ACQUIRED`
- 頻度: 営業日日次
- 経路: S&P Global Energy LNG Market DataのJKM現物評価・履歴。配信契約後に対象評価コード、API又はファイル仕様、内部モデル利用権を確定する。公開された無認証の同一日次履歴URLは未確認。
- 残件: JKM現物の歴史データと日次更新の権限未確保。公開の商品案内だけでは学習データにならない。
- 公表時刻・カットオフ: 2020-06-01公式案内の評価時刻16:30 Singapore=17:30 JST。現行の休日・訂正・配信時刻は契約仕様の再確認が必要。
- 15年以上のJKM履歴の提供案内を確認したが取得年数・件数の実測ではない。
- Henry Hub、TTF、JKM先物、月次輸入LNG平均価格は同一データではない。INPEXの長期LNG契約全量がスポットJKMに即時連動するわけではない。
- 公式根拠: [資料1](https://www.spglobal.com/energy/en/products-solutions/lng/lng-market-data), [資料2](https://energy.spglobal.com/platts-market-data.html), [資料3](https://press.spglobal.com/2020-06-01-S-P-Global-Platts-Launches-eWindow-for-Platts-JKM-LNG-Derivatives)

### lpg_afei — 東アジア着LPG価格・Argus AFEI

- 状態: `SUBSCRIPTION_REQUIRED_NO_HISTORY_ACQUIRED`
- 頻度: 営業日日次
- 経路: Argus International LPG。公式配信変更通知には ftp.argusmedia.com のDLPGフォルダ、dpg.csv が記載される。認証済み配信であり匿名FTPの実取得はしていない。
- 残件: 日次履歴・更新契約未確保。プロパン／ブタン、価格評価コード、単位、mid/bid/ask、受渡条件を採用時に固定する。
- 公表時刻・カットオフ: 未確認
- 2025-09-15付公式配信通知を確認。現行の時刻と履歴取得APIは未確認。
- 米国Mont BelvieuプロパンやSaudi CPをAFEIと同一扱いしない。
- 公式根拠: [資料1](https://www.argusmedia.com/en/methodology/key-commodity-prices/argus-far-east-index-afei), [資料2](https://www.argusmedia.com/en/solutions/products/argus-international-lpg), [資料3](https://www1.argusmedia.com/ArgusStaticContent/Resources/data/announcements/pdf/20250915lpg.pdf), [資料4](https://www.argusmedia.com/-/media/project/argusmedia/mainsite/english/documents-and-files/methodology/argus-international-lpg.pdf)

### audusd — 豪ドル／米ドル

- 状態: `PUBLIC_ROUTE_DOCUMENTED_FETCH_BLOCKED`
- 頻度: 日次
- 経路: RBA公式F11.1の日次CSV・XLS。CSV https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv 、XLS https://www.rba.gov.au/statistics/tables/xls-hist/2023-current.xls 。
- 残件: 公式ダウンロードリンクは実在するが当環境の無認証HTTP取得は403。取得済み件数は0、運用経路としての疎通確認が必要。
- 公表時刻・カットオフ: 公式頻度表はDaily 5:00pm。Sydneyの夏時間を含む時差を適用し、08:30 JSTでは直近の公開済み値を使用。
- USD per 1 AUDを使い、逆数のAUD per USDと混同しない。日中の定時参考値と市場終値は観測時刻が異なる。
- 実測: GET https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv — HTTP 403（2026-10-15/16）
- 実測: GET https://www.rba.gov.au/statistics/tables/xls-hist/2023-current.xls — HTTP 403（2026-10-15/16）
- 公式根拠: [資料1](https://www.rba.gov.au/statistics/tables/), [資料2](https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv), [資料3](https://www.rba.gov.au/statistics/tables/xls-hist/2023-current.xls), [資料4](https://www.rba.gov.au/statistics/tables/frequency-statistical-releases.html)

### container_freight_fbx — コンテナ運賃・Freightos Baltic Index

- 状態: `SUBSCRIPTION_API_AUTH_REQUIRED`
- 頻度: 営業日日次
- 経路: Freightos Terminal及びFBX API。公開ドキュメント https://apidocs.freightos.com/reference/get_fbx-data 。対象グローバル／航路tickerと期間queryは契約仕様で固定。
- 残件: APIキーと歴史データ利用権未確保。ウェブ掲載の最新値・短期チャートを完全な日次履歴とみなさない。
- 公表時刻・カットオフ: 公式料金ページFAQ: 算出06:00 UTC、公開14:00 UTC=23:00 JST。翌朝のカットオフには直近公表分。
- Terminalの個人プラン価格とAPI契約価格・必要履歴範囲は同一とは限らない。価格見積や申込みは未実施。
- 実測: GET https://api.freightos.com/fd_external_apis/fbx/data — HTTP 401 Unauthorized（2026-10-15/16）
- 公式根拠: [資料1](https://www.freightos.com/enterprise/terminal/pricing/), [資料2](https://www.freightos.com/enterprise/terminal/freightos-baltic-index-global-container-pricing-index/), [資料3](https://apidocs.freightos.com/reference/get_fbx-data)

### airfreight_fax — 航空貨物運賃・Freightos Air Index

- 状態: `SUBSCRIPTION_API_AUTH_REQUIRED`
- 頻度: 日次
- 経路: Freightos FAX APIのbig_regions / global。実在ドキュメント https://apidocs.freightos.com/reference/get_fax-big-regions 。
- 残件: API認証・対象航路・履歴契約未確保。
- 公表時刻・カットオフ: FAX独自の正確な公表時刻は未確認。FBXの14:00 UTCを流用しない。
- Baltic Air Freight Index BAIは週次で、日次FAXの代替取得とは数えない。
- 実測: GET https://api.freightos.com/fd_external_apis/fax/big_regions — HTTP 401 Unauthorized（2026-10-15/16）
- 公式根拠: [資料1](https://www.freightos.com/enterprise/terminal/fax-global-freightos-air-index/), [資料2](https://www.freightos.com/enterprise/terminal/pricing/), [資料3](https://apidocs.freightos.com/reference/get_fax-big-regions)

### baltic_dry_index_spot — バルチック海運指数・現物BDI

- 状態: `SUBSCRIPTION_REQUIRED_NO_HISTORY_ACQUIRED`
- 頻度: 営業日日次
- 経路: Baltic Exchange購読・履歴・APIサービス。対象指数又は航路評価を固定した上で配信契約を取得。
- 残件: 匿名の同一歴史系列ダウンロードは未確認。APIエンドポイント、認証、保持できる履歴範囲は未確保。
- 公表時刻・カットオフ: 評価ごとに公表時刻・休日を契約仕様で確認。forwardの17:00 Londonをspotに流用しない。
- BDRYは貨物先物ETFであり現物BDIではない。
- LNGはLNG1-174等の航路・船型変更履歴、航海日数及び$/dayか$/MMBtuかを固定する。
- 化学品・植物油BCAAは週次であり、日次clean tankerと同一ではない。
- 公式根拠: [資料1](https://www.balticexchange.com/en/data-services/market-information0/indices.html), [資料2](https://www.balticexchange.com/en/apply-page.html)

### baltic_capesize_index — ケープサイズ運賃・BCI

- 状態: `SUBSCRIPTION_REQUIRED_NO_HISTORY_ACQUIRED`
- 頻度: 営業日日次
- 経路: Baltic Exchange購読・履歴・APIサービス。対象指数又は航路評価を固定した上で配信契約を取得。
- 残件: 匿名の同一歴史系列ダウンロードは未確認。APIエンドポイント、認証、保持できる履歴範囲は未確保。
- 公表時刻・カットオフ: 評価ごとに公表時刻・休日を契約仕様で確認。forwardの17:00 Londonをspotに流用しない。
- BDRYは貨物先物ETFであり現物BDIではない。
- LNGはLNG1-174等の航路・船型変更履歴、航海日数及び$/dayか$/MMBtuかを固定する。
- 化学品・植物油BCAAは週次であり、日次clean tankerと同一ではない。
- 公式根拠: [資料1](https://www.balticexchange.com/en/data-services/market-information0/indices.html), [資料2](https://www.balticexchange.com/en/apply-page.html)

### lng_shipping_rate — LNG船運賃・Baltic LNG

- 状態: `SUBSCRIPTION_REQUIRED_NO_HISTORY_ACQUIRED`
- 頻度: 営業日日次
- 経路: Baltic Exchange購読・履歴・APIサービス。対象指数又は航路評価を固定した上で配信契約を取得。
- 残件: 匿名の同一歴史系列ダウンロードは未確認。APIエンドポイント、認証、保持できる履歴範囲は未確保。
- 公表時刻・カットオフ: 評価ごとに公表時刻・休日を契約仕様で確認。forwardの17:00 Londonをspotに流用しない。
- BDRYは貨物先物ETFであり現物BDIではない。
- LNGはLNG1-174等の航路・船型変更履歴、航海日数及び$/dayか$/MMBtuかを固定する。
- 化学品・植物油BCAAは週次であり、日次clean tankerと同一ではない。
- 公式根拠: [資料1](https://www.balticexchange.com/en/data-services/market-information0/gas-services.html), [資料2](https://www.balticexchange.com/en/apply-page.html)

### dirty_tanker_freight — 原油タンカー運賃・Baltic Dirty

- 状態: `SUBSCRIPTION_REQUIRED_NO_HISTORY_ACQUIRED`
- 頻度: 営業日日次
- 経路: Baltic Exchange購読・履歴・APIサービス。対象指数又は航路評価を固定した上で配信契約を取得。
- 残件: 匿名の同一歴史系列ダウンロードは未確認。APIエンドポイント、認証、保持できる履歴範囲は未確保。
- 公表時刻・カットオフ: 評価ごとに公表時刻・休日を契約仕様で確認。forwardの17:00 Londonをspotに流用しない。
- BDRYは貨物先物ETFであり現物BDIではない。
- LNGはLNG1-174等の航路・船型変更履歴、航海日数及び$/dayか$/MMBtuかを固定する。
- 化学品・植物油BCAAは週次であり、日次clean tankerと同一ではない。
- 公式根拠: [資料1](https://www.balticexchange.com/en/data-services/market-information0/tankers-services.html), [資料2](https://www.balticexchange.com/en/apply-page.html)

### clean_tanker_freight — 石油製品タンカー運賃・Baltic Clean

- 状態: `SUBSCRIPTION_REQUIRED_NO_HISTORY_ACQUIRED`
- 頻度: 営業日日次
- 経路: Baltic Exchange購読・履歴・APIサービス。対象指数又は航路評価を固定した上で配信契約を取得。
- 残件: 匿名の同一歴史系列ダウンロードは未確認。APIエンドポイント、認証、保持できる履歴範囲は未確保。
- 公表時刻・カットオフ: 評価ごとに公表時刻・休日を契約仕様で確認。forwardの17:00 Londonをspotに流用しない。
- BDRYは貨物先物ETFであり現物BDIではない。
- LNGはLNG1-174等の航路・船型変更履歴、航海日数及び$/dayか$/MMBtuかを固定する。
- 化学品・植物油BCAAは週次であり、日次clean tankerと同一ではない。
- 公式根拠: [資料1](https://www.balticexchange.com/en/data-services/market-information0/tankers-services.html), [資料2](https://www.balticexchange.com/en/apply-page.html)

### bunker_vlsfo — 船舶燃料油・VLSFO

- 状態: `SUBSCRIPTION_REQUIRED_NO_HISTORY_ACQUIRED`
- 頻度: 営業日日次
- 経路: RIM Bunker及び有料historical data。Singapore等の港、0.5%硫黄VLSFO、単位USD/tonne、供給条件を固定。
- 残件: 公式レポートは会員・購読案内。無認証の全履歴CSV/APIは未確認。
- 公表時刻・カットオフ: 正確な定刻未確認。日々の公開タイムスタンプでカットオフを判定。
- RIMは380cst/VLSFO/LSMGO等を掲載。LNGバンカーは週次であり別系列。
- 公式根拠: [資料1](https://eng.rim-intelligence.co.jp/report/bunker-en/), [資料2](https://www.rim-intelligence.co.jp/eng/contents/historical.html)

### dubai_crude — Dubai原油現物価格

- 状態: `SUBSCRIPTION_REQUIRED_NO_HISTORY_ACQUIRED`
- 頻度: 営業日日次
- 経路: RIM Crude/Condensate又はPlatts Dubaiの契約済み評価。出所を固定して歴史データ購入・配信仕様を確認。
- 残件: 同一定義の日次履歴未確保。
- 公表時刻・カットオフ: 正確な公表時刻未確認。
- 月次Dubai平均やTOCOM Dubai先物は現物の日次評価と異なる。明示的な設計変更なしに同一IDへ挿入しない。
- 公式根拠: [資料1](https://eng.rim-intelligence.co.jp/report/crude-condensate-en/), [資料2](https://www.rim-intelligence.co.jp/eng/contents/historical.html)

### japan_refining_margin — 日本の石油精製マージン

- 状態: `COMPONENT_CONTRACT_AND_FORMULA_REQUIRED`
- 頻度: 日次成分から計算／企業実績は四半期
- 経路: RIM ProductsとCrude/Condensate等の整合した成分から事前固定式を計算する。会社公表の実現マージンそのものが日次であるとは確認していない。
- 残件: 製品構成比、原油バスケット、歩留まり、単位、運賃・税・補助金・為替処理と過去成分が未確定。
- 公表時刻・カットオフ: 成分のうち最も遅い公開時刻をavailable_atとする。
- 製品価格−原油価格という単純式を、企業の実現利益率と表示しない。US gasoline-WTIを日本精製マージンに代替しない。
- 公式根拠: [資料1](https://eng.rim-intelligence.co.jp/report/products-en/), [資料2](https://eng.rim-intelligence.co.jp/report/crude-condensate-en/), [資料3](https://www.rim-intelligence.co.jp/eng/contents/historical.html)

### px_naphtha_spread — パラキシレン・ナフサ価格差

- 状態: `COMPONENT_CONTRACT_AND_FORMULA_REQUIRED`
- 頻度: 日次評価成分から計算
- 経路: RIM Petrochemicalsの対象アジアPXと、同じ受渡地域・基準日のnaphtha評価からUSD/tonne差を計算。
- 残件: 成分の対象グレード・CFR/FOB、履歴、利用権未確保。
- 公表時刻・カットオフ: 双方が公開された後のみ使用。
- 企業の稼働率、原料歩留まり、販売条件まで表す日次実現利益ではない。
- 公式根拠: [資料1](https://eng.rim-intelligence.co.jp/report/petrochemicals-en/), [資料2](https://eng.rim-intelligence.co.jp/report/products-en/), [資料3](https://www.rim-intelligence.co.jp/eng/contents/historical.html)

### ethylene_naphtha_spread — エチレン・ナフサ価格差

- 状態: `COMPONENT_CONTRACT_AND_FORMULA_REQUIRED`
- 頻度: 日次評価成分から計算
- 経路: RIM Petrochemicalsの対象アジアethyleneと、同じ受渡地域・基準日のnaphtha評価からUSD/tonne差を計算。
- 残件: 成分の対象グレード・CFR/FOB、履歴、利用権未確保。
- 公表時刻・カットオフ: 双方が公開された後のみ使用。
- 企業の稼働率、原料歩留まり、販売条件まで表す日次実現利益ではない。
- 公式根拠: [資料1](https://eng.rim-intelligence.co.jp/report/petrochemicals-en/), [資料2](https://eng.rim-intelligence.co.jp/report/products-en/), [資料3](https://www.rim-intelligence.co.jp/eng/contents/historical.html)

### thermal_coal_newcastle — 豪州Newcastle一般炭価格

- 状態: `SUBSCRIPTION_REQUIRED_NO_HISTORY_ACQUIRED`
- 頻度: NEWC指数は営業日日次／先物月次決済と区別
- 経路: globalCOAL NEWC Indexの契約。ICEのNewcastle先物は別instrumentとしてのみ検討可能。
- 残件: 同一仕様の匿名日次歴史データ未確保。
- 公表時刻・カットオフ: 現行のNEWC公表時刻は未確認。
- 熱量・水分・受渡条件を固定。月次World Bank石炭価格を日次へ水増ししない。
- 公式根拠: [資料1](https://www.globalcoal.com/coalprices/newcindex.cfm), [資料2](https://www.ice.com/products/1137/globalCOAL-)

### iron_ore — 鉄鉱石価格

- 状態: `EXACT_INSTRUMENT_AND_HISTORY_UNRESOLVED`
- 頻度: 現物評価又は先物営業日日次
- 経路: SGX鉄鉱石デリバティブ又はS&P現物IODEXの公式サービス。対象62%Fe等の品位とspot/futuresを設計で確定する。
- 残件: 現行IDだけでは商品が曖昧。匿名の完全な同一歴史系列取得は未検証。
- 公表時刻・カットオフ: 商品確定後に取引所・評価機関の公表時刻を設定。
- 取引所トップの遅延価格表示は履歴取得成功の証拠ではない。
- 公式根拠: [資料1](https://www.sgx.com/derivatives/products/iron-ore), [資料2](https://www.spglobal.com/energy/en/pricing-benchmarks/assessments/metals/iron-ore-index-iodex-price-explained)

### jepx_spot — JEPXスポット電力価格

- 状態: `PUBLIC_DOWNLOAD_VERIFIED`
- 頻度: 一日前市場、受渡日ごと48コマ、日次集計可能
- 経路: 公式CSV: POST https://www.jepx.jp/_download.php ; application/x-www-form-urlencoded body dir=spot_summary&file=spot_summary_YYYY.csv ; Referer https://www.jepx.jp/electricpower/market-data/spot/ 。GET https://www.jepx.jp/js/csv_read.php?dir=spot_summary&file=spot_summary_YYYY.csv も同Referer付きで成功。
- 残件: アクセス経路は確保。DB未投入、元ファイル保存・品質検査・公表時刻の確定は未実装。
- 公表時刻・カットオフ: 一日前の受渡価格。日付列は公表日ではなく受渡日。正確なサイト公開定刻は本調査では未確認。
- UTF-8又はCP932を判定。列: 受渡日, 時刻コード, 売り入札量(kWh), 買い入札量(kWh), 約定総量(kWh), システムプライス(円/kWh), 各9エリア価格等。
- 年度は4月起点。日次system価格=48コマの等時間算術平均と定義し、48件・コード1..48・欠損重複を検査。エリア価格とsystem価格を混在させない。
- 10月16日に取得したCSVに10月17日受渡分があることは、10月16日08:30時点で公表済みだった証拠にはならない。過去backtestには公表時刻保存又は保守的ラグが必要。
- 実測: GET https://www.jepx.jp/js/get_graph_year.php?dir=spot_summary — 200 body 2026,2005（2026-10-16）
- 実測: POST_DOWNLOAD https://www.jepx.jp/_download.php — 200 CSV; 2265696 bytes; 17520 data rows; 2025-04-01 to 2026-03-31（2026-10-16）
- 実測: POST_DOWNLOAD https://www.jepx.jp/_download.php — 200 CSV; 1260156 bytes; 9600 data rows; 2026-04-01 to 2026-10-17（2026-10-16）
- 実測: GET https://www.jepx.jp/js/csv_read.php?dir=spot_summary&file=spot_summary_2026.csv — 200 CSV; 9600 data rows. Earlier GET without Referer returned empty body; exact cause not isolated.（2026-10-16）
- 公式根拠: [資料1](https://www.jepx.jp/electricpower/market-data/spot/), [資料2](https://www.jepx.jp/js/spot_summary.js), [資料3](https://www.jepx.jp/js/get_graph_year.php?dir=spot_summary), [資料4](https://www.jepx.jp/electricpower/outline/), [資料5](https://www.jepx.jp/_download.php)

### company_monthly_fundamentals — 会社固有の月次・四半期実績

- 状態: `PUBLIC_DOCUMENTS_AVAILABLE_NOT_DAILY_REPLACEMENT`
- 頻度: 月次・四半期・年次、資料ごとに異なる
- 経路: NYK公式IR Data BookのPDF/ZIP、MOL公式Market Data PDF、各社決算資料を公開日単位で収集する。
- 残件: 公開資料は確認。各社各系列の表・単位・公開日時を固定した自動化は未実装。会社の生産量や平均実現価格が毎日更新されるわけではない。
- 公表時刻・カットオフ: NYK 2026-08-05公開、MOL 2026-10-13更新を公式掲載で確認。時刻不明の資料は翌営業日から利用する保守的ルールを推奨。
- 期末や月末に遡及して既知にしない。available_at以後のみas-of joinで保有し、age_daysを保存。日次へforward-fillしても独立した日次新規観測とは数えない。
- MOLのmonthly平均運賃やNYKのquarterly実績は事業検証に使えるが、未確保のFBX/BDI/JKM等と同一ではない。
- 公式根拠: [資料1](https://www.nyk.com/english/ir/library/fact/), [資料2](https://ir.mol.co.jp/en/ir/library/market.html), [資料3](https://www.inpex.com/english/ir/library/)

## INPEXを7群完備するための必要条件

必要群は日経先物、USD/JPY、Brent、JKM、AFEI、AUD/USD、米10年金利。既存4群の実データ品質は本調査で検査していない。JKMとAFEIの正規履歴・日次配信権限及びAUD/USDの実取得経路を確保し、全群について日付・公表時刻・最小学習期間の検査を通す必要がある。商品案内を見つけただけではデータ確保と数えない。

新規契約の営業窓口は上記公式商品ページ。契約後に必要なものは、評価コード、bid/ask/mid、受渡条件、休日、訂正履歴、APIまたはファイルの認証方式、履歴開始日、内部予測モデルへの利用範囲。申込み・購入・問い合わせ送信はしていない。

## 実装に渡す注意点

- HTTP200だけでは取得成功にしない。CSVヘッダ、非空データ、日付範囲、重複、数値単位を検査する。
- event_date、publication_at、retrieved_at、source_id、versionを別フィールドで保存。08:30JST予測へはpublication_at<=cutoffのみ結合。
- 履歴ファイルの現在取得を、当時リアルタイムに入手できた証拠として扱わない。
- 欠損は未確保とする。無関係な共通マクロや自社テクニカルで7〜10群を埋めない。
- 日々全群の係数を再推定しても、固定群のintraday予測力が実証されたことにはならない。

会社固有の経済的な関連性と、翌営業日の寄り付き以降を予測する実証的な有効性は別である。公開タイミングを守った時系列検証で確認する。

## 精度確認で判明した追加事項

- 鉄鉱石のPlatts IODEXは2026-01-02から62%Feではなく61%Fe。評価コードIODBZ00。過去と現在の仕様を記録する。[公式変更通知](https://www.spglobal.com/energy/en/pricing-benchmarks/our-methodology/subscriber-notes/070125-platts-to-update-iodex-quality-specifications-to-reflect-61-fe-effective-jan-2-2026)
- `lawson_same_store_sales`: 上場廃止後も日本語サイトに月次情報へのリンクあり。英語IRは2024年時点で止まるため最新ソースとして使わない。 状態: PUBLIC_MONTHLY_LIBRARY_CONFIRMED_NOT_PARSED。[公式資料1](https://www.lawson.co.jp/company/ir/)
- `familymart_same_store_sales`: 公式ページ2026-10-13更新、2026年9月度の既存店日商前年比101.6%。毎月10日前後公表。カード・チケット除外。100基準指数と+1.6%変化率の違いを固定。 状態: PUBLIC_HTML_TABLE_CONFIRMED_NOT_INGESTED。[公式資料1](https://www.family.co.jp/company/familymart/monthly_sales/flash.html), [公式資料2](https://www.family.co.jp/company/familymart/monthly_sales/transition.html)
- `domestic_it_sales`: 経産省旧特定サービス産業動態統計は2024年12月で終了。2025年1月から総務省サービス産業動態統計へ統合。情報サービス業の定義・基準・接続係数を確認せず継ぎ足さない。会社固有のCTC/SCSK売上そのものではない。 状態: PUBLIC_STATISTICS_WITH_SERIES_BREAK。[公式資料1](https://www.meti.go.jp/statistics/tyo/tokusabido/index.html), [公式資料2](https://www.stat.go.jp/data/mbss/index.html), [公式資料3](https://www.stat.go.jp/data/mbss/kekka.html)
- `construction_machinery_orders`: 日本建設機械工業会は出荷金額・国内出荷台数の年度別Excelを公開。出荷実績は受注とは異なるのでIDの指標定義を固定する。 状態: PUBLIC_MONTHLY_XLS_LINKS_DOCUMENTED_NOT_DOWNLOADED。[公式資料1](https://www.cema.or.jp/general/statistics/)
- `agriculture_input_sales`: Helenaの会社固有月次販売系列はこの調査で確保できなかった。米農業統計や穀物先物を同一実績にしない。 状態: EXACT_MONTHLY_COMPANY_SERIES_NOT_SECURED。[公式資料1](https://www.marubeni.com/en/ir/reports/integrated_report/)

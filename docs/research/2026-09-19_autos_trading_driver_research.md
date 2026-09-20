# 自動車5社・商社5社の銘柄別 driver 調査

調査日: 2026-09-19 JST。対象コード基準: fetched `origin/main` `0bf16a3a00f7ab8e4ee11a81bbc02e7321b896c5`。この資料は仮説登録であり、採用結果ではない。新規本番採用は **0**。引用した最新IR・ニュースを過去の予測日に既知だったと仮定しない。各 feature の符号は経済仮説であって Open→Close の確定的符号ではない。

## 既存資産から維持する知見

`docs/research/stock_indicator_research_2026-08-17.md`、`docs/FACTOR_FEASIBILITY.md`、`research/feature_sets.py`、`config/indicator_candidates.yaml`、`config/indicators.yaml`、`services/dataset.py` を確認した。以前の research mirror は production と指標・変換数・一部providerが一致せず、過去の数字を今回の本番対比として転記できない。既存報告の compact 化はMAE改善とBUY成績悪化が併存する。ADR除外も既存判断であり、経済的説明だけでは復活させない。

実装基準の現行共通指標 C は `vix, usdjpy, eurjpy, dollar_index, nikkei225_futures, sp500_futures, nasdaq100_futures, us_2y_yield, us_10y_yield, us_30y_yield`。自動車5社には同じ `xli, fxi, ewy, copper, wti` が加算され、商社5社には追加がない。TM/HMCのcatalogは存在するが `enabled: false` かつ `applies_to_tickers: []`。したがって「自動車がADRを現在使用」「商社が資源指標を現在使用」は誤り。以下の候補をcommonへ追加しない。

## 公式資料で確認した銘柄差

|Ticker|確認した事業・地域・利益情報|為替・資源・金利と会社のリスク|今回の研究候補 / 除外・保留|
|---|---|---|---|
|7203 トヨタ|2026-08-04 FY2027 Q1説明資料。北米・日本・欧州・アジア・その他、金融サービスを分離。HEV販売とvalue chain収益が寄与、営業利益約1.1兆円。中国事業利益は減少。|円安とコスト削減が寄与する一方、原材料・関税・中東物流・競争・資金調達がリスク。資料の為替差益は複数通貨を含むためUSDJPY単独の厳密感応度へ換算しない。|TMの1d return、USDJPY、XLIまたはSPY、米2Y変化。GM/F、WTI、銅、FXIは二段階候補。HMCやSuzukiの指標を自動付与しない。|
|7267 ホンダ|2026-08-05 Q1地域別外部顧客売上: 北米3,717.397 / 全体6,061.514十億円（計算61.3%）。四輪売上3,807.349、二輪1,141.131、金融1,026.137十億円。二輪のアジア売上615.198十億円。|北米四輪・金融とアジア二輪の二重構造。USDJPY、消費信用/金利、二輪需要が候補。エネルギー・原材料上昇の利益影響とrisk-onの同時変化を区別。|HMC return、USDJPY、米2Y変化、XLI、India ETFを個別比較。GM/Fは四輪に限ったproxyで二輪代替ではない。中国ETFを全自動車へ強制しない。|
|7201 日産|2026-08-03公式Q1資料、Re:Nissan回復計画を確認。Reutersの同日報道では営業利益779億円、前年791億円損失、米国・日本販売改善と世界販売減少が併存。|再建・コスト削減、米国価格/インセンティブ、中国競争、円、中東・原材料が主な仮説。通期/単発関税影響を日次の安定係数と同一視しない。|USDJPY、GM/Fのいずれか、XLI、FXI、米2Y変化。NSANYはOTC品質検証完了まで保留。Renault関係だけでRNOを採用しない。|
|7269 スズキ|最新FY2026 Q1公式資料: 全体売上1,705.8・営業利益158.0十億円。Maruti売上円換算849.4十億円（全体の約49.8%、単純比で内部消去未調整）、営業利益43.1十億円。Indiaの販売数量が増加。日本・欧州・アジアの利益構造を分離。|INRJPY、インド需要、原材料・燃料。高販売でもMaruti利益率は低下しており、単純India betaだけでは不十分。INRJPY不変でもUSDJPY変化と材料費影響がある。|Maruti現地株return、INDAまたはNifty Auto、INRJPY、USDJPY、WTI/アルミ品質確認候補。SZKMYの薄いOTCよりMarutiを優先して検証するが、自動採用なし。|
|7270 SUBARU|2026-08-05公表Q1: 売上1,250.9・営業利益42.6十億円、台数220千台のうち海外197千台。最新公式説明は米国の競争と販売維持を明示。海外比率を米国比率と誤記しない。|円安が寄与しても台数減、インセンティブ、材料費で営業利益44.3%減。米国需要・輸入関税・貴金属費がリスク。|USDJPY、GM/Fのいずれか、SPY、米2Y変化、VIX。CARZは中身と期間変更を確認後。中国/India/銅を自動追加しない。FUJHYはOTC保留。|
|8001 伊藤忠|2026-08-05 Q1説明: core利益249.5十億円中、非資源211.5（85%）。非資源は食品、流通、ICT、機械、北米電力等にまたがる。2026-08-06食品資料は食品core利益30.7十億円を開示。|資源価格+4.0、為替+12.0十億円という会社ブリッジ。食品原料高は原料販売と下流コストに反対方向へ作用。金利上昇は調達負担。資源株一括proxyは説明力不足。|USDJPY、消費/小売ETFの一つ、FXI、米2Y変化、必要なら穀物。鉄鉱石・原油は低優先の対照候補。銅・LNG全件追加なし。|
|8002 丸紅|2026-08-05 Q1 Q&A: 食料・アグリ調整利益33.0十億円。Helena/肥料流通、Creekstoneの牛肉加工、化学取引、航空、電力取引の収益差を確認。上流エネルギー関与は大きくないと会社が明記。|2026-05-01年次資料の年間利益感応度: 銅100USD/tあたり約13億円、原油1USD/bblあたり約2億円、USDJPY1円あたり約19億円、AUDJPY1円あたり約6億円。穀価上昇は農家所得に正、原料費には負で符号混合。|銅、USDJPY、AUDJPY、CornまたはSoybeans、米2Y変化。Natural Gas/WTIは低優先。穀物全種類や資源全種を一括追加しない。牛肉proxyはライセンス/履歴確認後。|
|8031 三井物産|2026-08-04 Q1利益294.1十億円: 金属資源61.2、エネルギー34.4、Mobility/Digital/Infrastructure73.0、Chemicals26.6、Wellness18.4、Innovation65.2、調整10.0。資産売却等もある。豪州・ブラジル鉄鉱石、南米銅、アジア/豪州/米国LNG・ガス。|年間利益感応度: 鉄鉱石1USD/t=30億円、銅100USD/t=5億円、USDJPY1円=46億円、AUDJPY1円=18億円。原油反映に月次ラグ。Henry Hubでの直接販売Exposureは限定的と明記。|鉄鉱石（有償を含む正規履歴のみ）、銅、Brent、USDJPY、AUDJPY、FXI。NG=Fは米ガスproxyでLNGそのものではない。近時の資産売却益を恒久driverにしない。|
|8053 住友商事|2026-07-31 Q1、08-13更新資料: 利益190.1・基礎利益177.0十億円。鉄鋼、自動車、輸送建機、都市開発、通信、Digital AI、生活、資源、化学、エネルギーの10区分。米国・南米・豪州の銅資産を明示。|年間利益感応度: USDJPY1円=20億円、銅100USD/t=4.6億円、原料炭1USD/t=0.9億円、一般炭1USD/t=2.7億円、鉄鉱石1USD/t=4.4億円。Ambatovy売却損と税効果は一時要因。|USDJPY、銅、AUDJPY、米2Y変化、電力/設備proxyの一つ。LNG・ニッケルを旧保有関係だけで固定採用しない。|
|8058 三菱商事|2026-08-03 Q1資料（08-14更新）: Energy & Power Solution、Mineral ResourcesのほかMobility、Food、Smart-Life等。資源利益86.6十億円。LNGはAsia-PacificとNorth Americaを分離、豪州BMA原料炭・南米銅を明示。|銅100USD/t=26億円、鉄鉱石1USD/t=7.4億円の年間感応度。米シェール買収後にgas感応度を27→34億円/0.1USD/MMBtuへ更新。原油のLNG販売価格反映にはラグ、配当は決議時期にも依存。|銅、Brent、NG=F（米国Exposureとして）、USDJPY、AUDJPY、原料炭品質確認候補。一般炭を原料炭の同義語にしない。|

数量感応度の単位は会社資料の年度計画に対する年間利益の変化であり、当日株価の係数には使用しない。全社の地域別利益率・全segment構成比・全ての公式リスク原文を完全抽出した監査ではない。不明な比率は補っていない。

## 直近180日ニュースと仮説（2026-03-23〜09-19）

|銘柄|確認できた記事/会社発表・日付|仮説と本番への制約|
|---|---|---|
|7203|[Reuters / Investing.com, 2026-08-04](https://www.investing.com/news/stock-market-news/toyota-profit-drops-for-fifth-straight-quarter-on-china-slump-iran-war-impact-4832799)|円安、米国/中国販売差、中東・材料費を同時に評価。見出しsentimentを遡及生成しない。|
|7267|[Reuters / New Straits Times, 2026-08-05](https://www.nst.com.my/amp/business/corporate/2026/08/1504777/honda-raises-full-year-forecasts-posts-first-profit-rise-six)|弱い円と前年関税コストの不在が決算差。単発前年比要因を日次alpha扱いしない。|
|7201|[Reuters / MarketScreener, 2026-08-03](https://uk.marketscreener.com/news/nissan-posts-surprisingly-strong-first-quarter-operating-profit-ce7f50d8d08ffe22)|コスト管理・円・地域差。二次AI記事の799億円とReuters/会社の779億円が不一致のため、会社/Reuters優先。|
|7269|[Reuters / MarketScreener, 2026-07-31](https://ae.marketscreener.com/news/india-s-maruti-suzuki-posts-quarterly-profit-fall-ce7f50dbde88f320)|Marutiの材料費決済時期とマージン圧迫。アルミ追加は仮説のみ。|
|7270|[会社IRニュース一覧](https://www.subaru.co.jp/en/ir/)の2026-08-05決算、08-28月次生産/販売発表|米国販売・インセンティブ仮説。直近180日の独立Reuters個社記事は今回の検索では確認できず、取得済みと報告しない。|
|8001|[公式IRニュース](https://www.itochu.co.jp/en/ir/news/index.html)の2026-08-31 Dentsu Soken取引/提携、07-08 ITOCHU DAY|非資源・ICT Exposure変化を次のfeature reviewで扱う。過去の構成へ後付けしない。|
|8002|[公式IR 2026-08-05 Q&A](https://ssl4.eir-parts.net/doc/8002/ir_material_for_fiscal_ym11/209763/00.pdf)、09-09 IR Day|穀物価格、農家所得、肥料在庫、電力取引の異なる符号を区別。|
|8031|[公式2026-08-04資料](https://www.mitsui.com/jp/en/ir/library/meeting/pdf/en_273_1q_ppt.pdf)のガス投資と資産回転|LNG/ガス、資源と資産回転を分ける。買収や売却の当日イベントdummyは時点付きarchive完成まで保留。|
|8053|[公式2026-07-31資料、08-13更新](https://www.sumitomocorp.com/-/media/Files/hq/ir/report/summary/2026/2606Presentation.pdf?sc_lang=en)|Ambatovy撤退によるExposure変化。旧ニッケル仮説を機械的に継続しない。|
|8058|[公式2026-08-03資料、08-14更新](https://www.mitsubishicorp.com/jp/en/ir/library/meetings/pdf/260803/20260803e.pdf)|米ガス・LNG Canada、原料炭のIndia/China需給。NGや銅の銘柄固有テストを優先。|

商社5社を含む同期間の経済記事として [Reuters / Boursorama, 2026-05-01](https://www.boursorama.com/bourse/actualites/les-grandes-societes-commerciales-japonaises-s-attendent-a-des-benefices-records-mais-les-services-publics-prevoient-des-difficultes-alors-que-la-guerre-en-iran-pese-sur-l-economie-1cacba2e9684823faf5bb1bc3af32361) を確認。資源高の利益寄与と調達費負担が企業群で異なることが仮説。仏語自動翻訳掲載であり、詳細の数字は各社公式IRへ戻した。ニュース本文のproduction利用は **RESEARCH_ONLY**。published/available/retrieved timestamp、source、URL、hash、license、timezoneを備えるhistorical archiveは本作業では確認できていない。

## 08:30可用性・provider候補

「市場が閉じた」と「providerに届いた」は別。下記は市場時刻上の資格であり、historical `available_timestamp` の立証ではない。今回providerへ履歴probeしていないためmissing/stale率はすべて **NOT_MEASURED**。現在取得した履歴を当時取得済みとして書き込まない。

|候補|provider候補 / market|timezone・確定時刻|08:30判定 / 品質 / fallback|
|---|---|---|---|
|TM,HMC ADR; GM,F; SPY,XLI,FXI,INDA等|Yahoo日足、契約済ならEODHD / US上場|America/New_York 16:00、翌04:00 JST夏/05:00冬。短縮日は13:00 ET。|前の完了sessionのみ。観測/配信時刻確認が条件。未検証データはFREE_UNVERIFIED、代替feedはsource明示。|
|NSANY,SZKMY,FUJHY,ITOCY,MITSY,SSUMY等|OTC / Yahoo候補|米現地日付、最終約定の鮮度を別検査|取引出来高・無変化連続日・銘柄比率/分割・上場継続確認前は保留。無取引を有効新値と扱わない。MSBHFは外国普通株の可能性がありADRと断定しない。|
|Maruti `MARUTI.NS`|NSE正規EOD、Yahoo候補|Asia/Kolkata 15:30 IST =19:00 JST。closing sessionは16:00 ISTまで。|翌東京08:30まで時間上の余裕あり。NSE休場・特別session・配信遅延が条件。東京t-1終値後3.5hの情報を含むが、日足returnは純粋after-Tokyo returnではない。|
|Nifty Auto / India ETF|NSE指数正規履歴、米INDAの代替仮説|NSE=India、INDA=US session|INDAはUSD建てでFXを内包。Nifty Autoと同一series扱い禁止。|
|USDJPY,AUDJPY,USDINR,INRJPY|契約feedまたはYahoo snapshot候補|24h市場、timezone付きas-of quote|cutoff以前の保存snapshot/正当なhistorical quoteのみ。INRJPY合成はUSDJPY/USDINRを同時点で使用、両者availabilityのmaxを継承。|
|銅、WTI、Brent、米天然ガス、穀物|CME/ICE正規履歴、Yahoo futures候補|銘柄別session/settlement、America/Chicago等|取引所契約とprovider日足境界を別検証。rolling連続価格の遡及修正もversion化。NG=F≠JKM LNG。|
|鉄鉱石、原料炭、LNG JKM、アルミ|取引所/指数ライセンス済データ|取引所・指数ごとの公表時刻|無料安定履歴/availability未確認。PAID_CANDIDATEまたはUNAVAILABLEのまま。無理なscrapingや別commodityへの無記名代替なし。|
|US2Y/10Y変化|US Treasury既存feed|US日次公表、実際の取得時刻|水準%のpct_changeでなくbp差分。観測済過去時点のみ。金利三本+spreadの重複はtrain内clusterで検討。|

時刻根拠: [NSE Market Timings](https://www.nseindia.com/static/market-data/market-timings)、[NYSE trading-hours FAQ](https://www.nyse.com/publicdocs/nyse/NYSE_Extended_Hours_Trading_FAQ.pdf)、[NYSE core/early-close説明](https://beta.nyse.com/data-insights/night-moves-what-trades-and-when-in--the-overnight-market)。参照日2026-09-19。米夏時間の04:00 JSTと冬の05:00 JSTを固定05:00と書く既存researchコメントは正確でない。インドにDSTはなく、NSE calendarでsessionを合わせる。

ADRでは **ADR日次return** と為替returnをまず別々に比較する。ADR価格×USDJPY÷換算比率を朝の寄付や予測closeとして代入しない。US日足の全returnも前日の東京情報を含むため、寄りギャップ予測能力とOpen→Close追加能力を分けてOOSで測る。

## 個別候補セットの登録案

すべてresearch candidate。各行で先頭4〜6個から始め、全window展開をしない。feature selection・correlation cluster・scaler・calibratorをouter foldのtraining内だけでfitする。候補集合は2026-09-19時点の知識なので、既存期間の再検証結果はdevelopmentであり新しいsealed holdoutではない。

|Ticker|優先して比較する最小候補|低優先/保留|本番selected|
|---|---|---|---|
|7203|TM 1d, USDJPY 1d, XLI 1d, US2Y bp|GM/F, WTI, FXI, 銅|現行 C+自動車5指標を維持、promotionなし|
|7267|HMC 1d, USDJPY 1d, US2Y bp, XLI 1d|INDA, GM/F, WTI|同上|
|7201|USDJPY 1d, GM/F 1d, XLI 1d, FXI 1d|NSANY, Renault, 銅|同上|
|7269|Maruti 1d, INRJPY 1d, INDA 1d, USDJPY 1d|Nifty Auto, WTI, アルミ, OTC|同上|
|7270|USDJPY 1d, GM/F 1d, US2Y bp, SPY 1d, VIX level|CARZ, WTI, FUJHY|同上|
|8001|USDJPY 1d, consumer/retail proxy 1d, FXI 1d, US2Y bp|穀物, 鉄鉱石, WTI|現行 Cのみ維持、promotionなし|
|8002|銅1d, USDJPY 1d, Corn/Soybeans 1d, AUDJPY 1d|米2Y, WTI/NG, cattle|同上|
|8031|銅1d, Brent 1d, USDJPY 1d, AUDJPY 1d|鉄鉱石, FXI, NG, LNG|同上|
|8053|銅1d, USDJPY 1d, US2Y bp, AUDJPY 1d|電力proxy, FXI, coal; nickel低優先|同上|
|8058|銅1d, Brent 1d, NG 1d, USDJPY 1d, AUDJPY 1d|原料炭, FXI, JKM|同上|

想定符号: ADR/同業/株式indexはrisk-on継続なら正、寄付で織込めばゼロ/反転もある。円安は輸出/円換算利益に正だがOpen→Closeでは未確定。自動車の資源高はコスト面で負、商社上流は正、下流/取引マージンは混合。金利高は消費金融需要・調達費で負の仮説だが景況感との同時変化がある。係数の符号をこの仮説へ強制しない。

## 公式IR source ledger

1. Toyota: [FY2027 Q1、2026-08-04](https://global.toyota/pages/global_toyota/ir/financial-results/2027_1q_presentation_2_en.pdf)、[現行IR一覧](https://global.toyota/en/ir/financial-results/)。
2. Honda: [地域/事業売上、2026-08-05](https://global.honda/en/investors/library/financialresult/main/010/teaserItems3/015/linkList/01/link/FYE202703_1Q_financial_reference_e.pdf)、[SEC 6-K、2026-08-05](https://www.sec.gov/Archives/edgar/data/715153/000119312526333722/d371554d6k.htm)。
3. Nissan: [Q1、2026-08-03](https://www.nissan-global.com/EN/IR/FINANCIAL_RESULTS/ASSETS/DATA/2026/20261st_presentation_461_e.pdf)、[IR](https://www.nissan-global.com/EN/IR/)。PDFのテキスト抽出が不完全なため全segment数表の確認は未完。
4. Suzuki: [FY2026 Q1説明資料](https://www.globalsuzuki.com/ir/library/financialresults/pdf/2026/financial_presentation_1q_s.pdf)、[IR一覧](https://www.globalsuzuki.com/ir/library/financialresults/)。Maruti数表は07-31発表を参照と記載。
5. Subaru: [最新業績、2026-08-05](https://www.subaru.co.jp/en/ir/finance/latest-results.html)、[IRニュース](https://www.subaru.co.jp/en/ir/)。
6. Itochu: [Q1説明、2026-08-05](https://www.itochu.co.jp/en/ir/financial_statements/2027/__icsFiles/afieldfile/2026/08/05/27_1st_04_e_2.pdf)、[segment資料、2026-08-06](https://www.itochu.co.jp/en/ir/financial_statements/2027/__icsFiles/afieldfile/2026/08/06/27_1st_02_e.pdf)、[Financial Information Report、2026-06-12](https://www.itochu.co.jp/en/ir/download/__icsFiles/afieldfile/2026/06/12/FIR2026E.pdf)。
7. Marubeni: [Q1 Q&A、2026-08-05](https://ssl4.eir-parts.net/doc/8002/ir_material_for_fiscal_ym11/209763/00.pdf)、[FY2025/新年度感応度、2026-05-01](https://www.marubeni.com/en/news/2026/release/data/202605012-2E.pdf)、[IR更新](https://www.marubeni.com/en/ir/)。
8. Mitsui: [FY March 2027 Q1、2026-08-04](https://www.mitsui.com/jp/en/ir/library/meeting/pdf/en_273_1q_ppt.pdf)。感応度と反映ラグはp18、資源地域はp20、LNGはp22以降。
9. Sumitomo: [FY2026 Q1、2026-07-31/08-13更新](https://www.sumitomocorp.com/-/media/Files/hq/ir/report/summary/2026/2606Presentation.pdf?sc_lang=en)、[公表日確認](https://www.sumitomocorp.com/en/jp/ir/report)。
10. Mitsubishi: [FY2026 Q1、2026-08-03/08-14更新](https://www.mitsubishicorp.com/jp/en/ir/library/meetings/pdf/260803/20260803e.pdf)、[更新日確認](https://www.mitsubishicorp.com/jp/en/ir/library/)。資料改訂版は初回公表時の内容と同一と仮定しない。

## 未確認事項と採否ゲート

- 10社全ての公式IR・最新決算資料と、各社/業界の期間内ニュース仮説を記録した。全社の完全な地域別利益構成表、単位通貨ごとの感応度、全リスク章の抽出は未完。無い数値を業種平均で補わない。
- 公式ADRプログラムの現在の比率、OTC上場継続、CARZ構成変更、provider historical availabilityとlicenseは未検証。symbol案は登録前に再確認が必要。
- missing率、stale率、univariate OOS、sign stability、nested feature selection、全model比較は本資料で測定していない。全候補は **NOT_PROMOTED**。
- 最終3〜8 featuresを優先、上限は原則floor(n_train/10)。freeze/version登録後に毎日係数をfitする。20新規営業日未満のforward実績を完了と扱わない。

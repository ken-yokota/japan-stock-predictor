# 海運・エネルギー7社の指標設計案（2026-10-15）

会社の事業から決めた**外部7〜9指標群**を固定し、データが揃ってから係数を毎朝推定するための研究資料。自社株のテクニカルはこの数に含めず、同じ原系列の1日・5日・20日リターンも1群と数える。現時点では専門データが不足しているため、全7社の希望プロファイルは **INSUFFICIENT_DATA**。設定・コード・本番DBの変更、購入、登録は行っていない。

事業上の関連と、08:30以後の寄り→引け予測力は別である。以下の符号は採算・企業価値の方向仮説であり、ザラ場の符号や予測精度を保証しない。候補を1群に落とすCVや、欠けた固有ドライバーを無関係な共通マクロで埋める方法は提案しない。

## 確認範囲

`DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED` は公式説明で商品や頻度を確認したという意味で、API・履歴・利用権・毎朝の到着は未確認。`EXISTING_CONFIG_NOT_REFETCHED` は現行設定の確認のみ。裸の VERIFIED という取得済み宣言はしない。日時不明の公式ページには閲覧日を記載した。

主な修正点：BDRYは現物BDIではなく近い限月の運賃先物ETF。NYKのNCAは2025-08-01にANAへ移管済み。ENEOSのJX金属は2025-03-19から持分法会社。Baltic航空運賃とBCAA化学品船運賃は週次であり、日次と数えない。

## 9101 日本郵船：外部8群

ONE持分のコンテナ、ドライバルク、自動車船、LNGなどのエネルギー輸送と物流を別の収益経路として扱う。2025年8月のNCA移管後、航空価格はYusen Logistics等のフォワーディング需給・仕入価格の代理であり航空会社利益の代理ではない。

根拠：[NYK IR Fact Book / Data Book](https://www.nyk.com/english/ir/library/fact/)、[NYK Report 2025](https://www.nyk.com/english/ir/library/nyk/)、[NYK: NCA share exchange completed](https://www.nyk.com/english/news/2025/20250801_0.html)。

| ID / 日本語名 | 事業経路・符号（仮説） | 日次の候補データと取得状態 |
|---|---|---|
| `nikkei225_futures`<br>日経225先物 | 日本株全体のリスク選好・先物の寄り前情報を共通市場要因として分離する。<br>**符号:** 通常は正。ただし寄り付きで織込み済みならザラ場への符号は不定。 | Yahoo Finance（既存設定） / NIY=F<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `usdjpy`<br>ドル円 | ドル建て運賃/利益の円換算とドル建て費用のnet exposure。<br>**符号:** 円安（USDJPY上昇）は利益換算に通常プラス。ヘッジ・ドル費用を控除すると条件付き。 | Yahoo Finance（既存設定） / JPY=X<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `container_freight_fbx`<br>コンテナ運賃FBX | ONEのスポット市況とコンテナ需給が持分利益見通しに波及。<br>**符号:** 運賃上昇は通常正。長期契約・数量減・燃料費で条件付き。 | Freightos / Baltic Exchange / FBX Global または固定ONE航路構成のFBX01/03/11バスケットを1群<br>NEW_DATA_REQUIRED・営業日日次<br>[FREIGHTOS](https://www.freightos.com/enterprise/terminal/) [FREIGHTOS_PRICING](https://www.freightos.com/enterprise/terminal/pricing/) |
| `baltic_dry_index_spot`<br>実物ドライバルク運賃BDI | ドライバルク船の貨物需要と船腹需給を直接測る。<br>**符号:** 通常正。長期契約の比重で即時反応は弱まる。 | Baltic Exchange / Baltic Dry Index spot composite<br>NEW_DATA_REQUIRED・営業日日次<br>[BALTIC_IND](https://www.balticexchange.com/en/data-services/market-information0/indices.html) [BALTIC_BUY](https://www.balticexchange.com/en/apply-page.html) |
| `lng_shipping_rate`<br>LNG船スポット用船料 | LNG船の需給・更改運賃・船価期待。<br>**符号:** 通常正だが長期契約中心なら弱い。 | Baltic Exchange / 太平洋LNG輸送ルート：現行LNG1-174/BLNG系の接続仕様要確認<br>NEW_DATA_REQUIRED・営業日日次<br>[BALTIC_IND](https://www.balticexchange.com/en/data-services/market-information0/indices.html) [BALTIC_GAS](https://www.balticexchange.com/en/data-services/market-information0/gas-services.html) [BALTIC_BUY](https://www.balticexchange.com/en/apply-page.html) |
| `euro_auto_equity`<br>欧州自動車株指数（輸送需要の代理） | 自動車船・自動車物流の荷主需要期待を欧州OEM株で近似。<br>**符号:** 通常正。輸出量とは一致せず関税・現地生産で逆転し得る。 | STOXX / EURO STOXX Automobiles & Parts, SXAK<br>NEW_DATA_REQUIRED・営業日日次<br>[STOXX_AUTO](https://stoxx.com/index/sxak/) |
| `airfreight_fax`<br>航空貨物運賃FAX | 航空フォワーディングの数量需要/仕入運賃環境。<br>**符号:** 需要主導の上昇なら正、仕入運賃だけ上昇し転嫁できなければ負。 | Freightos / Freightos Air Index: fixed Asia export basket as one group<br>NEW_DATA_REQUIRED・営業日日次<br>[FREIGHTOS](https://www.freightos.com/enterprise/terminal/) [FREIGHTOS_PRICING](https://www.freightos.com/enterprise/terminal/pricing/) |
| `bunker_vlsfo`<br>船舶燃料VLSFO | 船舶運航の燃料費とサーチャージの時間差。<br>**符号:** 費用として負。転嫁・ヘッジで中立化。 | RIM Intelligence / Singapore VLSFO; 航路構成固定なら主要港バスケットを1群<br>NEW_DATA_REQUIRED・営業日日次<br>[RIM_BUNKER](https://eng.rim-intelligence.co.jp/report/bunker-en/) [RIM_HISTORY](https://www.rim-intelligence.co.jp/eng/contents/historical.html) |

**限界と追加確認**
- 海運3社を同じBDRY+WTIの構成にしない。
- ONEの配当受領は四半期/イベント情報。日次独立系列には水増ししない。

**既存データの粗い代理（希望群の自動代替にはしない）**
- `baltic_dry_index`：ドライバルク需給の粗い代理。コンテナ・LNG船・自動車船・スポットBDIの同一系列ではない。
- `brent`：燃料コストの粗い方向感。VLSFOの精製/港湾basis。

## 9104 商船三井：外部9群

ONEとドライバルクに加え、LNG、原油・製品タンカー、化学品輸送/タンクターミナル、Daibiru不動産を持つ。Phase 2の安定収益ポートフォリオを踏まえ輸送モードを分離し、化学品専用運賃が未取得である事実も残す。

根拠：[BLUE ACTION 2035 Phase 2](https://ir.mol.co.jp/en/ir/ir_news/ir_news8663222873625829669/main/0/link/%28E%29BA2035%20Phase2.pdf)、[MOL Foundation Day CEO message](https://www.mol.co.jp/en/pr/2026/26026.html)、[MOL REPORT 2026](https://ir.mol.co.jp/en/ir/library/integrated_report.html)。

| ID / 日本語名 | 事業経路・符号（仮説） | 日次の候補データと取得状態 |
|---|---|---|
| `nikkei225_futures`<br>日経225先物 | 日本株全体のリスク選好・先物の寄り前情報を共通市場要因として分離する。<br>**符号:** 通常は正。ただし寄り付きで織込み済みならザラ場への符号は不定。 | Yahoo Finance（既存設定） / NIY=F<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `usdjpy`<br>ドル円 | ドル建て運賃/利益の円換算とドル建て費用のnet exposure。<br>**符号:** 円安（USDJPY上昇）は利益換算に通常プラス。ヘッジ・ドル費用を控除すると条件付き。 | Yahoo Finance（既存設定） / JPY=X<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `container_freight_fbx`<br>コンテナ運賃FBX | ONEの持分利益と世界コンテナ市況。<br>**符号:** 通常正、契約・運航費で条件付き。 | Freightos / Baltic Exchange / FBX Global または固定ONE航路構成のFBX01/03/11バスケットを1群<br>NEW_DATA_REQUIRED・営業日日次<br>[FREIGHTOS](https://www.freightos.com/enterprise/terminal/) [FREIGHTOS_PRICING](https://www.freightos.com/enterprise/terminal/pricing/) |
| `baltic_dry_index_spot`<br>実物ドライバルク運賃BDI | ドライバルクの短中期需給・傭船採算。<br>**符号:** 通常正だが長期契約/自社・傭船構成に依存。 | Baltic Exchange / Baltic Dry Index spot composite<br>NEW_DATA_REQUIRED・営業日日次<br>[BALTIC_IND](https://www.balticexchange.com/en/data-services/market-information0/indices.html) [BALTIC_BUY](https://www.balticexchange.com/en/apply-page.html) |
| `lng_shipping_rate`<br>LNG船スポット用船料 | LNG船とLNGインフラの更改・新規投資の期待採算。<br>**符号:** 正を想定。ただし長期契約で日次感応度は限定。 | Baltic Exchange / 太平洋LNG輸送ルート：現行LNG1-174/BLNG系の接続仕様要確認<br>NEW_DATA_REQUIRED・営業日日次<br>[BALTIC_IND](https://www.balticexchange.com/en/data-services/market-information0/indices.html) [BALTIC_GAS](https://www.balticexchange.com/en/data-services/market-information0/gas-services.html) [BALTIC_BUY](https://www.balticexchange.com/en/apply-page.html) |
| `dirty_tanker_freight`<br>原油タンカー運賃 | 原油輸送の運賃・タンカー需給。<br>**符号:** 通常正。ton-mile増加と荷動き減少を区別。 | Baltic Exchange / BDTI / TD3C MEG-China TCE; 定義を固定<br>NEW_DATA_REQUIRED・営業日日次<br>[BALTIC_IND](https://www.balticexchange.com/en/data-services/market-information0/indices.html) [BALTIC_TANK](https://www.balticexchange.com/en/data-services/market-information0/tankers-services.html) [BALTIC_BUY](https://www.balticexchange.com/en/apply-page.html) |
| `clean_tanker_freight`<br>石油製品タンカー運賃 | 製品タンカーの需給を独立して捉える。<br>**符号:** 通常正。化学品専用運賃への代替とはみなさない。 | Baltic Exchange / BCTI / TC1 or TC5 MEG-Japan; 定義を固定<br>NEW_DATA_REQUIRED・営業日日次<br>[BALTIC_IND](https://www.balticexchange.com/en/data-services/market-information0/indices.html) [BALTIC_TANK](https://www.balticexchange.com/en/data-services/market-information0/tankers-services.html) [BALTIC_BUY](https://www.balticexchange.com/en/apply-page.html) |
| `bunker_vlsfo`<br>船舶燃料VLSFO | 各船種の燃料費と転嫁タイミング。<br>**符号:** 通常負、TCEとの重複・契約転嫁に注意。 | RIM Intelligence / Singapore VLSFO; 航路構成固定なら主要港バスケットを1群<br>NEW_DATA_REQUIRED・営業日日次<br>[RIM_BUNKER](https://eng.rim-intelligence.co.jp/report/bunker-en/) [RIM_HISTORY](https://www.rim-intelligence.co.jp/eng/contents/historical.html) |
| `tse_reit_office`<br>東証REITオフィス指数 | Daibiru等のオフィス不動産価値・キャップレート期待。<br>**符号:** 通常正。海外物件/賃貸契約の相違で限定的。 | JPX / TSE REIT Office Index<br>NEW_DATA_REQUIRED・営業日日次<br>[JPX_REIT](https://www.jpx.co.jp/english/markets/indices/line-up/files/e_fac2_11_reitsec.pdf) [JPX_REIT_VALUES](https://www.jpx.co.jp/english/markets/indices/realvalues/index.html) |

**限界と追加確認**
- 化学品物流の専用BCAAは週次であり本日次9群には含めない。追加取得後は公表時刻付き低頻度要因として別管理。
- 自動車船運賃・LBC稼働率・長期契約比率も必要だが、利用できる日次系列が確認できないため一覧を偽って埋めない。

**既存データの粗い代理（希望群の自動代替にはしない）**
- `baltic_dry_index`：BDRYによるdry freight futures proxy。dirty/clean tanker、LNG船、化学品船。
- `brent`：燃料価格の粗いproxy。タンカー運賃・VLSFO・不動産価値。

## 9107 川崎汽船：外部8群

成長の柱である自動車船・石炭鉄鉱石船・LNG船に、ONE持分を分けて対応する。石炭鉄鉱石の長期安定収益を踏まえ、鉱価上昇をそのまま利益増と決め付けない。

根拠：[K LINE REPORT 2025](https://www.kline.co.jp/en/ir/library/report/main/01111111118/teaserItems2/0/linkList/02/link/KL_AR2025EN-1.pdf)、[K LINE business briefing Q&A](https://www.kline.co.jp/en/ir/library/event/bb_qa_2025.html)。

| ID / 日本語名 | 事業経路・符号（仮説） | 日次の候補データと取得状態 |
|---|---|---|
| `nikkei225_futures`<br>日経225先物 | 日本株全体のリスク選好・先物の寄り前情報を共通市場要因として分離する。<br>**符号:** 通常は正。ただし寄り付きで織込み済みならザラ場への符号は不定。 | Yahoo Finance（既存設定） / NIY=F<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `usdjpy`<br>ドル円 | ドル建て運賃/利益の円換算とドル建て費用のnet exposure。<br>**符号:** 円安（USDJPY上昇）は利益換算に通常プラス。ヘッジ・ドル費用を控除すると条件付き。 | Yahoo Finance（既存設定） / JPY=X<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `container_freight_fbx`<br>コンテナ運賃FBX | ONEの市況・持分利益期待。<br>**符号:** 通常正。自営の自動車船収益とは別経路。 | Freightos / Baltic Exchange / FBX Global または固定ONE航路構成のFBX01/03/11バスケットを1群<br>NEW_DATA_REQUIRED・営業日日次<br>[FREIGHTOS](https://www.freightos.com/enterprise/terminal/) [FREIGHTOS_PRICING](https://www.freightos.com/enterprise/terminal/pricing/) |
| `baltic_capesize_index`<br>ケープサイズ運賃 | 大型鉄鉱石等の船腹需給・更改価格。<br>**符号:** 通常正だが長期専用船は短期影響が弱い。 | Baltic Exchange / Baltic Capesize Index / 5TC（同一群で整合する定義を固定）<br>NEW_DATA_REQUIRED・営業日日次<br>[BALTIC_IND](https://www.balticexchange.com/en/data-services/market-information0/indices.html) [BALTIC_BUY](https://www.balticexchange.com/en/apply-page.html) |
| `iron_ore`<br>鉄鉱石価格 | 鉄鋼原料荷主の需要と鉄鉱石輸送数量の期待。<br>**符号:** 需要増による鉱価上昇なら正、鉱山供給障害なら輸送減で負もある。 | SGX / licensed iron ore assessment provider / 62% Fe iron ore futures daily settlement; 契約・code・roll未確定<br>NEW_DATA_REQUIRED・営業日日次<br>[IRON](https://www.sgx.com/) |
| `euro_auto_equity`<br>欧州自動車株指数（輸送需要の代理） | 完成車・High & Heavy輸送の荷主需要期待。<br>**符号:** 通常正だが関税・現地化と地域偏重に依存。 | STOXX / EURO STOXX Automobiles & Parts, SXAK<br>NEW_DATA_REQUIRED・営業日日次<br>[STOXX_AUTO](https://stoxx.com/index/sxak/) |
| `lng_shipping_rate`<br>LNG船スポット用船料 | LNG輸送の新規/更改採算・船腹需給。<br>**符号:** 通常正。新船の初期金利/償却負担と長期契約を別扱い。 | Baltic Exchange / 太平洋LNG輸送ルート：現行LNG1-174/BLNG系の接続仕様要確認<br>NEW_DATA_REQUIRED・営業日日次<br>[BALTIC_IND](https://www.balticexchange.com/en/data-services/market-information0/indices.html) [BALTIC_GAS](https://www.balticexchange.com/en/data-services/market-information0/gas-services.html) [BALTIC_BUY](https://www.balticexchange.com/en/apply-page.html) |
| `bunker_vlsfo`<br>船舶燃料VLSFO | 自動車船/バルク等の燃料費。<br>**符号:** 費用として負。燃料転嫁やLNG燃料への転換で変化。 | RIM Intelligence / Singapore VLSFO; 航路構成固定なら主要港バスケットを1群<br>NEW_DATA_REQUIRED・営業日日次<br>[RIM_BUNKER](https://eng.rim-intelligence.co.jp/report/bunker-en/) [RIM_HISTORY](https://www.rim-intelligence.co.jp/eng/contents/historical.html) |

**限界と追加確認**
- BCIと鉄鉱石価格は運賃と貨物価値という異なる経路。ただし共通中国需要を二重に評価しないよう相関・寄与を監視。
- 石炭輸送数量、PCC契約運賃、LNG長期契約比率は別途低頻度・イベント情報を取得。

**既存データの粗い代理（希望群の自動代替にはしない）**
- `baltic_dry_index`：BDRYはCapesizeを含むdry futures proxy。BCI現物系列と同一ではない。
- `fxi`：中国需要の広い株式代理。鉄鉱石貨物量や鉄鉱石価格。

## 1605 INPEX：外部7群

原油・LNG上流企業。豪州IchthysではLNGだけでなくLPG・コンデンセートを生産するため、原油、北東アジアLNG、LPG、豪ドル操業費を分離する。油価/為替感応度は公式に開示されるが長期契約と税制で日次感応度は薄まる。

根拠：[INPEX Integrated Report 2025: Growth Strategy](https://www.inpex.com/assets/documents/english/ir/library/integrated_report/inpex_integratedreport202512_en_03.pdf)、[INPEX business risks](https://www.inpex.com/english/ir/risk.html)、[Ichthys LNG Project](https://www.inpex.com/english/business/project/ichthys.html)。

| ID / 日本語名 | 事業経路・符号（仮説） | 日次の候補データと取得状態 |
|---|---|---|
| `nikkei225_futures`<br>日経225先物 | 日本株全体のリスク選好・先物の寄り前情報を共通市場要因として分離する。<br>**符号:** 通常は正。ただし寄り付きで織込み済みならザラ場への符号は不定。 | Yahoo Finance（既存設定） / NIY=F<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `usdjpy`<br>ドル円 | ドル建て油ガス販売収入の円換算とUSD資産負債評価。<br>**符号:** 通常円安が正。為替ヘッジ/費用/税で変わる。 | Yahoo Finance（既存設定） / JPY=X<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `brent`<br>ブレント原油先物 | 上流の原油・コンデンセート販売単価と油価連動LNG契約。<br>**符号:** 通常正。契約ラグ、地域basis、税で弱まる。 | Yahoo Finance（既存設定） / BZ=F<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `lng_jkm`<br>北東アジアLNG価格JKM | 北東アジアLNG需給とスポット/契約更新価値、国内輸入LNG原料費。<br>**符号:** 販売には正、国内調達には負。長期油価連動が多く単純比例不可。 | S&P Global Platts / JKM spot assessment, Northeast Asia delivered LNG<br>NEW_DATA_REQUIRED・営業日日次<br>[JKM](https://press.spglobal.com/2020-06-01-S-P-Global-Platts-Launches-eWindow-for-Platts-JKM-LNG-Derivatives) |
| `lpg_afei`<br>極東LPG価格AFEI | IchthysのLPG副産物販売とアジアLPG需給。<br>**符号:** 通常正。実現売価・販売先/契約条件による。 | Argus / AFEI propane/butane; 販売構成が確認できる場合のみ固定バスケット1群<br>NEW_DATA_REQUIRED・営業日日次<br>[LPG](https://www.argusmedia.com/en/methodology/key-commodity-prices/argus-far-east-index-afei) [LPG_PRODUCT](https://www.argusmedia.com/en/solutions/products/argus-international-lpg) |
| `audusd`<br>豪ドル/米ドル | 豪州操業・建設のAUDコストをUSD販売収入と比較。<br>**符号:** AUD高はコスト面で通常負。通貨ヘッジと豪州資産換算で変動。 | Reserve Bank of Australia / Units of USD per AUD, daily table<br>NEW_DATA_REQUIRED・営業日日次<br>[RBA](https://www.rba.gov.au/statistics/frequency/exchange-rates.html) |
| `us_10y_yield`<br>米10年国債利回り | 長期大型油ガス投資の割引率・USD資金調達環境。<br>**符号:** 金利上昇は価値/費用面で通常負。実際の契約金利ではない。 | US Treasury / Daily Treasury Par Yield Curve: 10 Yr<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>[UST](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?page=0&type=daily_treasury_yield_curve) |

**限界と追加確認**
- Henry HubをJKMと同義にしない。LPGをLNGの別テクニカル列として水増ししない。
- LPG価格群は実際の副産物事業を根拠とするが利益寄与の定量確認が必要。

**既存データの粗い代理（希望群の自動代替にはしない）**
- `natural_gas`：米ガス市況・世界LNG裁定の粗いproxy。アジアJKMと同義ではない。
- `audjpy`：豪州通貨の円換算。AUDUSD操業費exposure。USDJPYと併用する場合の変換/共線性を明示。

## 5020 ENEOSホールディングス：外部8群

国内石油精製販売のマージンと原油在庫、石化、電力/LNG、持分法JX金属を分ける。2025年3月以後のJX金属は完全連結ではないため、古い銅中心のモデルを機械的に継承しない。

根拠：[ENEOS segment overview](https://www.hd.eneos.co.jp/english/ir/investor/keyword/segment.html)、[ENEOS integrated report release](https://www.hd.eneos.co.jp/news/release_information/year/2025/20250930_01_01_mr02/)、[ENEOS FY2026 corporate value materials](https://www.hd.eneos.co.jp/english/about/corporate_value/pdf/e_hd_en_ot_fy2026_01.pdf)。

| ID / 日本語名 | 事業経路・符号（仮説） | 日次の候補データと取得状態 |
|---|---|---|
| `nikkei225_futures`<br>日経225先物 | 日本株全体のリスク選好・先物の寄り前情報を共通市場要因として分離する。<br>**符号:** 通常は正。ただし寄り付きで織込み済みならザラ場への符号は不定。 | Yahoo Finance（既存設定） / NIY=F<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `usdjpy`<br>ドル円 | 輸入原油の円コスト、在庫評価、海外利益換算・販売価格転嫁の時間差。<br>**符号:** 一意に固定不可。原料高は負、在庫/海外利益は正になり得る。 | Yahoo Finance（既存設定） / JPY=X<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `dubai_crude`<br>ドバイ原油価格 | 輸入原油調達・在庫評価・E&P採算の共通油価水準。<br>**符号:** 在庫/E&Pには正、調達コストには負。販売転嫁ラグで変わる。 | RIM / Platts / Dubai crude daily assessment<br>NEW_DATA_REQUIRED・営業日日次<br>[RIM_CRUDE](https://eng.rim-intelligence.co.jp/report/crude-condensate-en/) [RIM_HISTORY](https://www.rim-intelligence.co.jp/eng/contents/historical.html) |
| `japan_refining_margin`<br>日本の石油製品・原油マージン | 国内ガソリン等の販売価格と原料コスト差が精製販売採算を左右。<br>**符号:** 通常正。税/補助金/在庫を除いた定義と数量条件が必要。 | RIM Intelligence / 日本ガソリン/灯油/軽油の固定製品構成価格 − lagged Dubai/JCC代替調達原価 − 輸送費<br>NEW_DERIVED_DATA_REQUIRED・営業日日次<br>[RIM_PRODUCTS](https://eng.rim-intelligence.co.jp/report/products-en/) [RIM_CRUDE](https://eng.rim-intelligence.co.jp/report/crude-condensate-en/) [RIM_HISTORY](https://www.rim-intelligence.co.jp/eng/contents/historical.html) |
| `px_naphtha_spread`<br>パラキシレン・ナフサ価格差 | 芳香族などアジア石化の製品原料spread。<br>**符号:** 通常正。高機能材全体をPXだけで説明しない。 | RIM / Platts / アジアPX価格 − 同時点ナフサ価格（同一通貨/単位、場所・歩留まりを確認）<br>NEW_DERIVED_DATA_REQUIRED・営業日日次<br>[RIM_PETCHEM](https://eng.rim-intelligence.co.jp/report/petrochemicals-en/) [RIM_PRODUCTS](https://eng.rim-intelligence.co.jp/report/products-en/) [RIM_HISTORY](https://www.rim-intelligence.co.jp/eng/contents/historical.html) |
| `jepx_spot`<br>日本卸電力スポット価格 | 電力の発電・小売に対する国内卸売価格環境。<br>**符号:** net発電なら正、net調達なら負。固定契約等で条件付き。 | JEPX / System Price / disclosed operating-area fixed mix, 30-min values aggregated to one daily group<br>NEW_DATA_REQUIRED・毎日・30分値から日平均<br>[JEPX](https://www.jepx.jp/electricpower/market-data/spot/) |
| `lng_jkm`<br>北東アジアLNG価格JKM | LNG原料調達/電源コストと上流ガス価値。<br>**符号:** 調達側負・上流側正。契約構成のnet exposureで決まる。 | S&P Global Platts / JKM spot assessment, Northeast Asia delivered LNG<br>NEW_DATA_REQUIRED・営業日日次<br>[JKM](https://press.spglobal.com/2020-06-01-S-P-Global-Platts-Launches-eWindow-for-Platts-JKM-LNG-Derivatives) |
| `jx_advanced_metals_equity`<br>JX金属株価 | 持分法投資JX金属の事業価値・資本配分期待。<br>**符号:** 通常正。ENEOS持株比率変更と共通株式市場因子を管理。 | JPX / licensed Japanese equity data provider / 東京証券取引所 5016（Yahooコードは未検証）<br>NEW_DATA_REQUIRED・営業日日次<br>[ENEOS_SEG](https://www.hd.eneos.co.jp/english/ir/investor/keyword/segment.html) [ENEOS_2026](https://www.hd.eneos.co.jp/english/about/corporate_value/pdf/e_hd_en_ot_fy2026_01.pdf) |

**限界と追加確認**
- JX金属は2025-03-19以前に公開株価がなく、過去期間への後方補間禁止。
- 国内精製マージンをXLEやWTI単独で代替しない。

**既存データの粗い代理（希望群の自動代替にはしない）**
- `brent`：世界原油水準。Dubai輸入原価basis・国内精製マージン。
- `xle`：米エネルギー株の投資家評価。ENEOS国内精製/電力/JX金属の固有ドライバー。

## 5019 出光興産：外部8群

国内精製販売に加えて豪州石炭、海外油ガス、基礎化学、潤滑油など機能材、電力を持つ。ENEOS/コスモと同じ原油一式にせず、豪州一般炭とAUDコスト、エチレン原料spreadを含める。

根拠：[Idemitsu risk information](https://www.idemitsu.com/en/ir/management/risk/index.html)、[Idemitsu segment information FY2025 / year ended March 2026](https://www.idemitsu.com/en/ir/finance/segmentinformation/index.html)、[Idemitsu Integrated Report library](https://www.idemitsu.com/en/ir/library/annual/index.html)。

| ID / 日本語名 | 事業経路・符号（仮説） | 日次の候補データと取得状態 |
|---|---|---|
| `nikkei225_futures`<br>日経225先物 | 日本株全体のリスク選好・先物の寄り前情報を共通市場要因として分離する。<br>**符号:** 通常は正。ただし寄り付きで織込み済みならザラ場への符号は不定。 | Yahoo Finance（既存設定） / NIY=F<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `usdjpy`<br>ドル円 | 輸入原油の円コスト、在庫評価、海外利益換算・販売価格転嫁の時間差。<br>**符号:** 一意に固定不可。原料高は負、在庫/海外利益は正になり得る。 | Yahoo Finance（既存設定） / JPY=X<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `brent`<br>ブレント原油先物 | ノルウェー等の上流販売価格・原油在庫の広域基準。<br>**符号:** 上流には正、精製原料には負。粗い基準で地域basisを残す。 | Yahoo Finance（既存設定） / BZ=F<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `japan_refining_margin`<br>日本の石油製品・原油マージン | 国内燃料販売価格と調達原価の差。<br>**符号:** 通常正。販売time lag、在庫・税/補助金で見え方が変わる。 | RIM Intelligence / 日本ガソリン/灯油/軽油の固定製品構成価格 − lagged Dubai/JCC代替調達原価 − 輸送費<br>NEW_DERIVED_DATA_REQUIRED・営業日日次<br>[RIM_PRODUCTS](https://eng.rim-intelligence.co.jp/report/products-en/) [RIM_CRUDE](https://eng.rim-intelligence.co.jp/report/crude-condensate-en/) [RIM_HISTORY](https://www.rim-intelligence.co.jp/eng/contents/historical.html) |
| `ethylene_naphtha_spread`<br>エチレン・ナフサ価格差 | アジア基礎化学の需給とクラッカー採算。<br>**符号:** 通常正。設備余剰・副産物・定修で変動。 | RIM / Platts / CFR Northeast Asia ethylene − CFR Japan naphtha（原料歩留まり/単位調整）<br>NEW_DERIVED_DATA_REQUIRED・営業日日次<br>[RIM_PETCHEM](https://eng.rim-intelligence.co.jp/report/petrochemicals-en/) [RIM_PRODUCTS](https://eng.rim-intelligence.co.jp/report/products-en/) [RIM_HISTORY](https://www.rim-intelligence.co.jp/eng/contents/historical.html) |
| `thermal_coal_newcastle`<br>豪州一般炭NEWC価格 | 豪州自社炭鉱の一般炭販売価格。<br>**符号:** 通常正。長期販売・炭質・税・操業費で変動。 | ICE / globalCOAL / Newcastle Coal Futures front-month daily settlement or licensed NEWC daily physical; choose one<br>NEW_DATA_REQUIRED・営業日日次<br>[COAL](https://www.ice.com/products/1137/globalCOAL-) [COAL_INDEX](https://www.globalcoal.com/coalprices/newcindex.cfm) |
| `audusd`<br>豪ドル/米ドル | 豪州炭鉱のAUD操業費に対するUSD商品収入。<br>**符号:** AUD高はコスト面で通常負。換算・ヘッジを控除する必要。 | Reserve Bank of Australia / Units of USD per AUD, daily table<br>NEW_DATA_REQUIRED・営業日日次<br>[RBA](https://www.rba.gov.au/statistics/frequency/exchange-rates.html) |
| `jepx_spot`<br>日本卸電力スポット価格 | 電力/再エネ事業の売電・調達環境。<br>**符号:** 事業のnet positionとFIT/FIP/PPAに依存。 | JEPX / System Price / disclosed operating-area fixed mix, 30-min values aggregated to one daily group<br>NEW_DATA_REQUIRED・毎日・30分値から日平均<br>[JEPX](https://www.jepx.jp/electricpower/market-data/spot/) |

**限界と追加確認**
- 潤滑油・機能材は独自の販売spread/原料価格と需要データを追加要請。XLIを同じものとみなさない。
- Nghi Sonの稼働/配当・資金負担は別の四半期/イベント要因として管理。

**既存データの粗い代理（希望群の自動代替にはしない）**
- `wti`：世界原油水準。豪州石炭・石化マージン。
- `audjpy`：豪州の通貨proxy。USD販売対AUDコストの定義を省略した代用。

## 5021 コスモエネルギーホールディングス：外部7群

国内の石油精製販売と石化、UAEの油田、再エネを組み合わせる。原油価格の上流利益と精製原料/在庫影響を分け、PX採算・電力市場・国内金利を含める。SAFは事業開始を確認したが、利益規模と日次価格系列が未確定なので7群の水増しには使わない。

根拠：[COSMO REPORT 2025](https://www.cosmo-energy.co.jp/content/dam/corp/jp/en/ir/report/2025/pdf/report2025_en_all.pdf)、[COSMO REPORT 2025 official contents](https://www.cosmo-energy.co.jp/en/ir/library/annual.html)。

| ID / 日本語名 | 事業経路・符号（仮説） | 日次の候補データと取得状態 |
|---|---|---|
| `nikkei225_futures`<br>日経225先物 | 日本株全体のリスク選好・先物の寄り前情報を共通市場要因として分離する。<br>**符号:** 通常は正。ただし寄り付きで織込み済みならザラ場への符号は不定。 | Yahoo Finance（既存設定） / NIY=F<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `usdjpy`<br>ドル円 | 輸入原油の円コスト、在庫評価、海外利益換算・販売価格転嫁の時間差。<br>**符号:** 一意に固定不可。原料高は負、在庫/海外利益は正になり得る。 | Yahoo Finance（既存設定） / JPY=X<br>EXISTING_CONFIG_NOT_REFETCHED・営業日日次<br>現行 config/indicators.yaml |
| `dubai_crude`<br>ドバイ原油価格 | UAE上流販売の地域原油水準と輸入原油調達・在庫。<br>**符号:** 上流/在庫には正、調達には負。原油grade差・OPEC/操業量を別管理。 | RIM / Platts / Dubai crude daily assessment<br>NEW_DATA_REQUIRED・営業日日次<br>[RIM_CRUDE](https://eng.rim-intelligence.co.jp/report/crude-condensate-en/) [RIM_HISTORY](https://www.rim-intelligence.co.jp/eng/contents/historical.html) |
| `japan_refining_margin`<br>日本の石油製品・原油マージン | 国内精製販売の製品価格と原料原価の差。<br>**符号:** 通常正。稼働率・販売構成・在庫/補助金で条件付き。 | RIM Intelligence / 日本ガソリン/灯油/軽油の固定製品構成価格 − lagged Dubai/JCC代替調達原価 − 輸送費<br>NEW_DERIVED_DATA_REQUIRED・営業日日次<br>[RIM_PRODUCTS](https://eng.rim-intelligence.co.jp/report/products-en/) [RIM_CRUDE](https://eng.rim-intelligence.co.jp/report/crude-condensate-en/) [RIM_HISTORY](https://www.rim-intelligence.co.jp/eng/contents/historical.html) |
| `px_naphtha_spread`<br>パラキシレン・ナフサ価格差 | 石油化学の芳香族/PX需給と原料spread。<br>**符号:** 通常正。中国増設/定修/歩留まりによる。 | RIM / Platts / アジアPX価格 − 同時点ナフサ価格（同一通貨/単位、場所・歩留まりを確認）<br>NEW_DERIVED_DATA_REQUIRED・営業日日次<br>[RIM_PETCHEM](https://eng.rim-intelligence.co.jp/report/petrochemicals-en/) [RIM_PRODUCTS](https://eng.rim-intelligence.co.jp/report/products-en/) [RIM_HISTORY](https://www.rim-intelligence.co.jp/eng/contents/historical.html) |
| `jepx_spot`<br>日本卸電力スポット価格 | 風力等の再エネと電力販売の卸売価格環境。<br>**符号:** 市場連動売電は正。FIT/PPA固定価格部分の感応度は小さい。 | JEPX / System Price / disclosed operating-area fixed mix, 30-min values aggregated to one daily group<br>NEW_DATA_REQUIRED・毎日・30分値から日平均<br>[JEPX](https://www.jepx.jp/electricpower/market-data/spot/) |
| `jgb_10y_yield`<br>日本10年国債利回り | 国内製油所/再エネの長期投資割引率・円資金調達環境。<br>**符号:** 金利上昇は通常負。固定/変動債務構成に依存。 | Ministry of Finance Japan / 10年定残存国債利回り（日次系列の正式URL/コードは未検証）<br>NEW_DATA_REQUIRED・営業日日次<br>[MOF](https://www.mof.go.jp/english/policy/jgbs/) |

**限界と追加確認**
- JGB金利は業務・資産評価の明示的経路を持つが国債利回りだけで企業の借入費用を表さない。
- SAF−UCO原料spreadを追加検討する場合、主力石油事業と同じ重みと決めず利益寄与と公表時刻を確認する。

**既存データの粗い代理（希望群の自動代替にはしない）**
- `brent`：世界原油水準のproxy。Dubai/UAE grade basis・国内精製margin。
- `us_10y_yield`：世界割引率のproxy。円の資金調達環境として無断代用しない。

## データ定義・08:30カットオフ・費用

営業日日次は毎暦日新しい値があることを意味しない。前回公表値の時刻と経過営業日を保存し、古い値を当日の観測と偽装しない。新しいsource_idを既存の代理系列と同じIDで上書きしない。

| 群 | 頻度・08:30利用条件 | 課金/アクセス・検証 |
|---|---|---|
| `nikkei225_futures` | 営業日日次。前完了セッションEOD、または実際に08:30までに観測したsnapshot。過去snapshotをEODから捏造しない。 | 既存無料経路。商用利用権/品質は別途確認<br>LOCAL_CONFIG_ONLY |
| `usdjpy` | 営業日日次。08:30までに観測済みの値、または前完了日EOD。 | 既存無料経路<br>LOCAL_CONFIG_ONLY |
| `brent` | 営業日日次。前完了セッションEOD、または08:30以前に観測済みsnapshot。rollと調整方針を固定。 | 既存無料経路<br>LOCAL_CONFIG_ONLY |
| `us_10y_yield` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 公式CSV/XML無料<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `container_freight_fbx` | 営業日日次。FBXは公式FAQで14:00 UTC公表（23:00 JST）。前日公表分を翌08:30に使用。週次FBXと混在不可。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `baltic_dry_index_spot` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `baltic_capesize_index` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `lng_shipping_rate` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `euro_auto_equity` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `airfreight_fax` | 営業日日次。日次更新は確認。FAX固有の公表時刻は未確認。保存済み公表タイムスタンプが前08:30以前の最新値だけを使用。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `bunker_vlsfo` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `dirty_tanker_freight` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `clean_tanker_freight` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `tse_reit_office` | 営業日日次。日本株前営業日の確定終値のみ。 | 公開表示あり。長期履歴・機械利用はライセンス/ベンダー条件確認<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `iron_ore` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>INSTRUMENT_DOCUMENTED_EXACT_FEED_UNVERIFIED |
| `lng_jkm` | 営業日日次。公表方式は16:30 Singapore（17:30 JST）。予測日の前公表営業日分を08:30に使用。現在の配信契約時刻を再確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `lpg_afei` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `audusd` | 営業日日次。豪州前営業日公表分。予測当日の豪州午後値を朝に使わない。 | 公式公開表無料。規約・履歴ファイル・自動取得可否は確認<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `dubai_crude` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `japan_refining_margin` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `px_naphtha_spread` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `ethylene_naphtha_spread` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `jepx_spot` | 毎日・30分値から日平均。受渡日と公表日を区別。08:30時点で既に公表されていた受渡日の価格のみ。保守的初期運用は前受渡日の確定値。 | 公式公開データ。機械利用条件と履歴取得を要確認<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `jx_advanced_metals_equity` | 営業日日次。日本株前営業日の確定終値のみ。上場前系列は存在せず後方補間しない。 | 市場データの既存利用権を確認。新API取得は未実施<br>BUSINESS_LINK_VERIFIED_PRICE_FEED_UNVERIFIED |
| `thermal_coal_newcastle` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 有料契約・履歴/モデル利用権の見積確認が必要<br>DOCUMENTATION_VERIFIED_ACCESS_UNVERIFIED |
| `jgb_10y_yield` | 営業日日次。前営業日の公表済み値のみ。08:30 JSTまでのavailable_at/first_observed_atを保存。正式な公表時刻は導入前に確認。 | 公式公開統計を候補。系列・取得経路の確認が必要<br>CANDIDATE_UNVERIFIED |

各指標の代理誤差：

- `nikkei225_futures`：既存config/indicators.yamlにはverifiedと記録。ただし本調査で値・履歴・API疎通・DBを再確認していない。
- `usdjpy`：既存config/indicators.yamlにはverifiedと記録。ただし本調査で値・履歴・API疎通・DBを再確認していない。
- `brent`：既存config/indicators.yamlにはverifiedと記録。ただし本調査で値・履歴・API疎通・DBを再確認していない。 現物Dubaiや全社実現油価そのものではない。
- `us_10y_yield`：資金調達・割引率の代理。企業固有の借入金利/信用スプレッドではない。
- `container_freight_fbx`：スポット40ft運賃。ONEの長期契約、航路構成、燃料サーチャージ、運賃認識の時間差がある。複数航路を群数に加算しない。
- `baltic_dry_index_spot`：既存baltic_dry_indexはBDRY先物ETFなので別ID。長期契約船の収益はスポットに即時比例しない。
- `baltic_capesize_index`：既存IDはpending。既存BDRYでこの固有市況を黙って置き換えない。
- `lng_shipping_rate`：LNG価格JKMとは別。長期用船が主体なら短期損益感応度は弱い。船型変更・旧BLNGコードとの接続を確認。
- `euro_auto_equity`：自動車メーカーへの需要期待の株式代理。実際の完成車輸出台数・PCC運賃ではなく、欧州偏重と株式市場共通因子がある。
- `airfreight_fax`：Baltic Air Freight BAIは週次なので代用しない。フォワーダーには仕入運賃高でもあり符号は条件付き。
- `bunker_vlsfo`：原油とは精製・港湾需給・規制の差がある。燃油サーチャージとヘッジが転嫁を緩和。LNG燃料価格は別途週次。
- `dirty_tanker_freight`：原油商品価格と運賃を区別。TCEは燃料コストを内包するためbunkerと二重計上に注意。
- `clean_tanker_freight`：実在する製品輸送ドライバー。化学品船の専用市況を表すものではなくBCAA欠落は別に残す。
- `tse_reit_office`：Daibiruのオフィス資産価値・割引率の代理。上場REITと非上場の海外物件収益は同一ではない。
- `iron_ore`：海運会社は鉱山会社ではない。鉱価上昇が供給障害なら貨物量に逆風となる。FXIや銅で鉄鉱石輸送固有の信号を置換しない。
- `lng_jkm`：米Henry Hub（既存natural_gas）とは異なる。長期油価連動LNG契約やヘッジで即時感応度は小さくなる。
- `lpg_afei`：LNGとLPGは別商品。Ichthysの実現価格・長期契約価格そのものではない。
- `audusd`：AUD建て操業費をUSD収入と対比する通貨ドライバー。AUDJPYとUSDJPYの二重計上を避け独立した定義にする。
- `dubai_crude`：輸入原油コスト・在庫効果と上流収益の両方向。Brent/WTIとは油種・地域basisが異なる。
- `japan_refining_margin`：研究用gross margin proxy。製品歩留まり、通貨/単位、税・補助金、販売時差を明示。会社実績マージンを日次補間して未来情報を混ぜない。各製品を別群として数えない。
- `px_naphtha_spread`：石化のgross spread代理で石油燃料の精製マージンとは別。grade/feed codesと個社の実際の製品構成は取得前に確認。
- `ethylene_naphtha_spread`：エチレン系設備の採算代理。中国設備過剰・副産物・定修により単純差と利益は一致しない。
- `jepx_spot`：発電の売電にはプラス、固定単価の小売調達にはマイナス。発電/小売のnet position、燃料費調整・FIT/FIP・PPAに依存。
- `jx_advanced_metals_equity`：持分法投資の価値変化の代理。ENEOSがJX金属を完全連結する前提は禁止。保有比率変更をas-of管理。
- `thermal_coal_newcastle`：最終決済の月平均を日次価格と混同しない。発熱量/灰分・販売契約・限月roll・操業費で個社損益が変わる。
- `jgb_10y_yield`：国内長期資金・割引率の代理。実際の借入満期・固定変動比率による。米2年利回りで穴埋めしない。

## 追加取得の依頼事項

購入・申込みは実施していない。下表は必要データと確認条件の一覧。

| 対象 | 必要データ・提供元 | 本当の頻度・費用 | 受け入れ条件 |
|---|---|---|
| 9101, 9104, 9107 | BDI/BCI、FBX、LNG運賃、dirty/clean tankerの時刻付き日次履歴<br>Baltic/Freightos<br>[BALTIC_BUY](https://www.balticexchange.com/en/apply-page.html) [FREIGHTOS_PRICING](https://www.freightos.com/enterprise/terminal/pricing/) | 営業日日次<br>有料。履歴/API/内部モデル用途の権利は見積確認。 | 最低120営業日より長い複数局面の履歴、定義改訂、祝日、公表時刻、訂正履歴を保存。業務利用契約未実施。 |
| 9104 | BCAA easy-chemical/vegetable-oil route rates、LBCターミナル稼働/料金<br>Baltic BCAA / MOL disclosures<br>[BALTIC_CHEM](https://www.balticexchange.com/en/data-services/market-information0/chemical-and-agricultural-oil-assessments-.html) [MOL_PLAN](https://ir.mol.co.jp/en/ir/ir_news/ir_news8663222873625829669/main/0/link/%28E%29BA2035%20Phase2.pdf) | BCAA週次、会社KPI四半期等<br>BCAA有料、会社IR無料 | 日次に水増しせず、直近公表値+経過日数として別管理。clean tankerは同義代替ではない。 |
| 9101, 9104, 9107 | PCC/PCTCチャーター料、実運賃、輸送台数、地域別輸出<br>Clarksons等を候補、個別購読商品/機械取得経路は未検証。会社IRを低頻度補助。<br>[NYK_FACT](https://www.nyk.com/english/ir/library/fact/) [KLINE_QA](https://www.kline.co.jp/en/ir/library/event/bb_qa_2025.html) [MOL_PLAN](https://ir.mol.co.jp/en/ir/ir_news/ir_news8663222873625829669/main/0/link/%28E%29BA2035%20Phase2.pdf) | 外部有料系列の頻度未確認、会社開示は月次/四半期等<br>外部は要照会、会社IR無料 | 日次取得可能と断定しない。欧州自動車株は明示的な需要proxyで運賃に改名しない。 |
| 5020, 5019, 5021 | 日本製品価格、Dubai、freight、PX/ethylene/naphtha、補助金・税・原料調達ラグ<br>RIM / Platts; 政府制度は別の公表イベント<br>[RIM_PRODUCTS](https://eng.rim-intelligence.co.jp/report/products-en/) [RIM_CRUDE](https://eng.rim-intelligence.co.jp/report/crude-condensate-en/) [RIM_PETCHEM](https://eng.rim-intelligence.co.jp/report/petrochemicals-en/) [RIM_HISTORY](https://www.rim-intelligence.co.jp/eng/contents/historical.html) | 価格は営業日日次、制度変更はイベント<br>価格履歴は有料。RIM公開説明に日次レポート年額料金あり、履歴/API別途。 | 通貨/単位/地域/時刻を一致させgross proxyと実績利益を明確に分離。 |
| 9101, 9104, 9107, 1605, 5020 | VLSFO、JKM、AFEI、LNG bunkering<br>RIM / Platts / Argus<br>[RIM_BUNKER](https://eng.rim-intelligence.co.jp/report/bunker-en/) [JKM](https://press.spglobal.com/2020-06-01-S-P-Global-Platts-Launches-eWindow-for-Platts-JKM-LNG-Derivatives) [LPG_PRODUCT](https://www.argusmedia.com/en/solutions/products/argus-international-lpg) | VLSFO/JKM/AFEI日次、RIM LNG bunkering週次<br>有料 | 日次Henry HubをアジアLNGに偽装しない。実際の公開・受信時刻を保存。 |
| 5021, 5020, 5019 | SAF fob Singapore、日本着プレミアム、UCO原料、ジェット燃料、補助制度<br>Argus / RIM候補<br>[SAF](https://view.argusmedia.com/saf-price-snapshot.html) [UCO](https://www.argusmedia.com/en/methodology/key-commodity-prices/argus-asian-uco-price-assessments) [COSMO_WEB](https://www.cosmo-energy.co.jp/en/ir/library/annual.html) | 商品存在は確認、各系列の日次頻度と日本basisは未検証。RIM liquid biofuelsは週次。<br>有料、契約未実施 | 少量事業の寄与を確認。未確認系列は7〜10群を満たすための埋草にしない。 |
| 9101, 9104, 9107, 1605, 5020, 5019, 5021 | 契約比率、契約ラグ、船隊、ヘッジ、製油所稼働/定修、油ガス生産、プロジェクト障害、決算・自己株買い<br>各社公式IR/TDnet、操業情報公開<br>[NYK_FACT](https://www.nyk.com/english/ir/library/fact/) [MOL_PLAN](https://ir.mol.co.jp/en/ir/ir_news/ir_news8663222873625829669/main/0/link/%28E%29BA2035%20Phase2.pdf) [KLINE_QA](https://www.kline.co.jp/en/ir/library/event/bb_qa_2025.html) [INPEX_RISK](https://www.inpex.com/english/ir/risk.html) [ENEOS_SEG](https://www.hd.eneos.co.jp/english/ir/investor/keyword/segment.html) [IDEMITSU_RISK](https://www.idemitsu.com/en/ir/management/risk/index.html) [COSMO_WEB](https://www.cosmo-energy.co.jp/en/ir/library/annual.html) | 四半期・年次・イベント<br>公式公表無料、構造化に開発費 | 公表日以降だけ有効。2026年の事業構造を2024年のバックテストへ遡及しない。 |
| 5020, 5019, 5021 | JEPX公式価格と公表時刻、MOF国債利回り<br>JEPX / MOF<br>[JEPX](https://www.jepx.jp/electricpower/market-data/spot/) [MOF](https://www.mof.go.jp/english/policy/jgbs/) | JEPX毎日30分、国債営業日日次候補<br>公開情報。機械利用の条件/履歴経路を確認 | JEPXの受渡日を公表日として使わない。国債系列は正式コード・配信時刻未検証。 |

## 検証に必要なこと

1. 未取得の必要群・履歴・公開時刻を揃える。日次レポートの購読だけでAPIや長期履歴が含まれるとみなさない。
2. 固定した会社別群で、時系列の外部検証を行う。期間別・相場局面別に、全群の係数・寄与・安定性、MSEと取引コスト後の指標を確認する。
3. 企業説明の正負と学習係数が違う場合は、自動削除より先に寄り付き織込み、価格転嫁ラグ、proxy誤差、共線性、データ時刻を調べる。
4. 2026年の事業構成・将来に判明した契約/稼働率を過去へ遡及しない。JX金属上場以前の株価や未観測snapshotを埋めない。

## 公式一次資料の出典台帳

全資料の閲覧日：2026-10-15。日付不明は公開日を推定していない。

- **NYK_FACT** — [NYK IR Fact Book / Data Book](https://www.nyk.com/english/ir/library/fact/)。公表/対象日：日付不明（継続更新ページ）。公式IRがコンテナ・物流・自動車・ドライバルク・エネルギーを別セグメントで開示。日付不明の継続更新ページ。
- **NYK_REPORT** — [NYK Report 2025](https://www.nyk.com/english/ir/library/nyk/)。公表/対象日：2025-11-26。英語版公開日。成長分野と安定利益化を確認。
- **NYK_NCA** — [NYK: NCA share exchange completed](https://www.nyk.com/english/news/2025/20250801_0.html)。公表/対象日：2025-08-01。NCAは同日以後NYK連結対象外。航空物流と航空会社の運賃収入を混同しない。
- **MOL_REPORT** — [MOL REPORT 2026](https://ir.mol.co.jp/en/ir/library/integrated_report.html)。公表/対象日：日付不明（継続更新ページ）。2026版の目次・事業一覧を閲覧。大容量PDF本文は取得上限で未取得。詳細根拠は次の公式Phase 2資料とCEOメッセージ。
- **MOL_PLAN** — [BLUE ACTION 2035 Phase 2](https://ir.mol.co.jp/en/ir/ir_news/ir_news8663222873625829669/main/0/link/%28E%29BA2035%20Phase2.pdf)。公表/対象日：2026-03-31。化学品物流でLBCターミナルと海上輸送を統合、Daibiru不動産などの事業別戦略。
- **MOL_CEO** — [MOL Foundation Day CEO message](https://www.mol.co.jp/en/pr/2026/26026.html)。公表/対象日：2026-04-01。エネルギー・化学品/自動車物流・不動産への展開とDaibiru等の安定収益寄与。
- **KLINE_REPORT** — [K LINE REPORT 2025](https://www.kline.co.jp/en/ir/library/report/main/01111111118/teaserItems2/0/linkList/02/link/KL_AR2025EN-1.pdf)。公表/対象日：2025-12-23。自動車船、石炭鉄鉱石、LNG輸送、ONEの事業説明。公開日は公式ニュース一覧。
- **KLINE_QA** — [K LINE business briefing Q&A](https://www.kline.co.jp/en/ir/library/event/bb_qa_2025.html)。公表/対象日：2025-06-03。自動車船運賃の投資規律、石炭鉄鉱石の安定収益、LNG新船の初期金利負担を説明。
- **INPEX_REPORT** — [INPEX Integrated Report 2025: Growth Strategy](https://www.inpex.com/assets/documents/english/ir/library/integrated_report/inpex_integratedreport202512_en_03.pdf)。公表/対象日：2026-07-07。2025年度を対象とする2026年公表版。油・天然ガス/LNGと長期開発事業。
- **INPEX_RISK** — [INPEX business risks](https://www.inpex.com/english/ir/risk.html)。公表/対象日：日付不明（継続更新ページ）。2026年度の油価感応度を掲載。国内天然ガスは国産ガスと輸入LNGを使用。
- **INPEX_ICHTHYS** — [Ichthys LNG Project](https://www.inpex.com/english/business/project/ichthys.html)。公表/対象日：日付不明（継続更新ページ）。豪州でLNG・LPG・コンデンセートを生産。大半のLNGは長期SPAでスポットJKMへの即時全量連動ではない。
- **ENEOS_SEG** — [ENEOS segment overview](https://www.hd.eneos.co.jp/english/ir/investor/keyword/segment.html)。公表/対象日：日付不明（継続更新ページ）。石油製品、高機能材料、電力、再エネ、E&P。JX金属は2025-03-19上場後持分法会社。
- **ENEOS_IR** — [ENEOS integrated report release](https://www.hd.eneos.co.jp/news/release_information/year/2025/20250930_01_01_mr02/)。公表/対象日：2025-09-30。ENEOS REPORT 2025公表。
- **ENEOS_2026** — [ENEOS FY2026 corporate value materials](https://www.hd.eneos.co.jp/english/about/corporate_value/pdf/e_hd_en_ot_fy2026_01.pdf)。公表/対象日：2026-05。JX金属株式処分など資本配分の更新がある。古い持株比率を固定しない。
- **IDEMITSU_RISK** — [Idemitsu risk information](https://www.idemitsu.com/en/ir/management/risk/index.html)。公表/対象日：日付不明（継続更新ページ）。豪州石炭、ノルウェー/東南アジア油ガス、アジア基礎化学、世界の潤滑油、為替・原油調達・Nghi Sonのリスク。
- **IDEMITSU_SEG** — [Idemitsu segment information FY2025 / year ended March 2026](https://www.idemitsu.com/en/ir/finance/segmentinformation/index.html)。公表/対象日：2026-03-31。事業構成確認に使用。決算対象期末でありページ公開日ではない。
- **IDEMITSU_REPORT** — [Idemitsu Integrated Report library](https://www.idemitsu.com/en/ir/library/annual/index.html)。公表/対象日：日付不明（継続更新ページ）。公式ページは動的表示のため本文未取得。具体的事業根拠は公式リスク・セグメント開示。
- **COSMO_REPORT** — [COSMO REPORT 2025](https://www.cosmo-energy.co.jp/content/dam/corp/jp/en/ir/report/2025/pdf/report2025_en_all.pdf)。公表/対象日：2025-11-26。石油精製・販売、石油化学、UAE上流、再エネ。石油事業は在庫影響を除く利益も開示。
- **COSMO_WEB** — [COSMO REPORT 2025 official contents](https://www.cosmo-energy.co.jp/en/ir/library/annual.html)。公表/対象日：2025-11-26。UAE油田、製油所効率、SAF量産、グリーン電力を確認。
- **BALTIC_IND** — [Baltic Exchange indices and route definitions](https://www.balticexchange.com/en/data-services/market-information0/indices.html)。公表/対象日：日付不明（継続更新ページ）。BDI、Capesize、clean/dirty、LNG等の定義。スポットBDIとBDRYは異なる。
- **BALTIC_GAS** — [Baltic gas freight services](https://www.balticexchange.com/en/data-services/market-information0/gas-services.html)。公表/対象日：日付不明（継続更新ページ）。LNG/LPG運賃評価。商品ガス価格とは別の輸送供給需要を測る。
- **BALTIC_TANK** — [Baltic tanker services](https://www.balticexchange.com/en/data-services/market-information0/tankers-services.html)。公表/対象日：日付不明（継続更新ページ）。日次physical/forward運賃、weekly資産価格、quarterly OPEXを区別。
- **BALTIC_BUY** — [Baltic data subscriptions and API](https://www.balticexchange.com/en/apply-page.html)。公表/対象日：日付不明（継続更新ページ）。有料購読・歴史データ・APIの案内。契約・認証・データ取得は未実施。
- **BALTIC_CHEM** — [Baltic Chemical and Agricultural Oil Assessments](https://www.balticexchange.com/en/data-services/market-information0/chemical-and-agricultural-oil-assessments-.html)。公表/対象日：日付不明（継続更新ページ）。BCAAは週次。日次データとして数えない。
- **FREIGHTOS** — [Freightos Terminal official product description](https://www.freightos.com/enterprise/terminal/)。公表/対象日：日付不明（継続更新ページ）。FBXとFAXの日次更新を明記。
- **FREIGHTOS_PRICING** — [Freightos Terminal packages and methodology FAQ](https://www.freightos.com/enterprise/terminal/pricing/)。公表/対象日：日付不明（継続更新ページ）。FBX日次算出・公開時刻を説明。FAXの公開時刻は別途確認が必要。サンプル/公開表示と履歴API利用権は別。
- **RIM_BUNKER** — [RIM Bunker Oil daily](https://eng.rim-intelligence.co.jp/report/bunker-en/)。公表/対象日：日付不明（継続更新ページ）。日次VLSFO/LSMGO/380cst。LNG bunkeringは週次と明記。閲覧時の表示料金は年額US$5,760(Web)、履歴/機械利用権は別途確認。
- **RIM_CRUDE** — [RIM Crude/Condensate daily](https://eng.rim-intelligence.co.jp/report/crude-condensate-en/)。公表/対象日：日付不明（継続更新ページ）。日次Dubai/Japan Dated Brent等。閲覧時の表示料金は年額US$5,760(Web)、履歴/機械利用権は別途確認。
- **RIM_PRODUCTS** — [RIM Products daily](https://eng.rim-intelligence.co.jp/report/products-en/)。公表/対象日：日付不明（継続更新ページ）。日本の内航製品価格、CFR輸入・FOB輸出価格を日次で提供。
- **RIM_PETCHEM** — [RIM Petrochemicals daily](https://eng.rim-intelligence.co.jp/report/petrochemicals-en/)。公表/対象日：日付不明（継続更新ページ）。アジア石化製品のdailyレポート。個別グレード/コード/過去系列は契約前確認が必要。
- **RIM_HISTORY** — [RIM historical data](https://www.rim-intelligence.co.jp/eng/contents/historical.html)。公表/対象日：日付不明（継続更新ページ）。原油・製品・燃料・LPG・LNG・石化・電力の日次/月次履歴を販売。
- **JKM** — [S&P Global official JKM methodology announcement](https://press.spglobal.com/2020-06-01-S-P-Global-Platts-Launches-eWindow-for-Platts-JKM-LNG-Derivatives)。公表/対象日：2020-06-01。北東アジア着LNGのdaily assessment、16:30 Singapore時点。現行feed/cadence再確認は導入時要。
- **LPG** — [Argus Far East Index (AFEI)](https://www.argusmedia.com/en/methodology/key-commodity-prices/argus-far-east-index-afei)。公表/対象日：日付不明（継続更新ページ）。東アジア着プロパン/ブタン価格を日次で公表。
- **LPG_PRODUCT** — [Argus International LPG](https://www.argusmedia.com/en/solutions/products/argus-international-lpg)。公表/対象日：日付不明（継続更新ページ）。日次サービス。購読・履歴・配信権は未契約。
- **RBA** — [RBA daily exchange rates](https://www.rba.gov.au/statistics/frequency/exchange-rates.html)。公表/対象日：日付不明（継続更新ページ）。AUD当たりUSDを含む日次公開表を実際に閲覧。機械取得試験は未実施。
- **UST** — [US Treasury daily par yield curves](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?page=0&type=daily_treasury_yield_curve)。公表/対象日：日付不明（継続更新ページ）。公式日次表・CSV/XML案内を閲覧。既存コードに米10年系列あり。
- **JEPX** — [JEPX spot market public data](https://www.jepx.jp/electricpower/market-data/spot/)。公表/対象日：日付不明（継続更新ページ）。30分値・日平均・エリア価格・ダウンロード項目を確認。実データダウンロードは未実施。
- **STOXX_AUTO** — [EURO STOXX Automobiles & Parts](https://stoxx.com/index/sxak/)。公表/対象日：日付不明（継続更新ページ）。公式指数ページ。欧州自動車株の代理であり世界自動車運賃指数ではない。
- **JPX_REIT** — [TSE REIT property sector index factsheet](https://www.jpx.co.jp/english/markets/indices/line-up/files/e_fac2_11_reitsec.pdf)。公表/対象日：日付不明（継続更新ページ）。Officeを含む不動産用途別指数。日次total return計算の説明。
- **JPX_REIT_VALUES** — [JPX published index values](https://www.jpx.co.jp/english/markets/indices/realvalues/index.html)。公表/対象日：日付不明（継続更新ページ）。TSE REIT Office Indexの公式掲載を確認。
- **IRON** — [SGX iron ore futures and settlements](https://www.sgx.com/)。公表/対象日：日付不明（継続更新ページ）。SGX公式トップがIron Ore Futuresのdaily settlement chartと説明。個別契約コードと履歴APIは未確認。
- **COAL** — [ICE globalCOAL Newcastle Coal Futures](https://www.ice.com/products/1137/globalCOAL-)。公表/対象日：日付不明（継続更新ページ）。公式契約仕様を確認。月平均NEWCで最終決済する先物で、日次settlement系列と月次最終決済は別。
- **COAL_INDEX** — [globalCOAL NEWC Index](https://www.globalcoal.com/coalprices/newcindex.cfm)。公表/対象日：日付不明（継続更新ページ）。NEWC日次提供と購読案内。取得・契約は未実施。
- **BDRY** — [Amplify Breakwave Dry Bulk Shipping ETF](https://amplifyetfs.com/bdry/)。公表/対象日：2026-09-29。閲覧ページのデータ基準日。近い限月のドライバルク先物ETFで、スポットBDIを追うものではない。
- **SAF** — [Argus SAF price assessments](https://view.argusmedia.com/saf-price-snapshot.html)。公表/対象日：日付不明（継続更新ページ）。SAF fob Singapore等の存在を確認。日本向けマージンのdaily頻度・権利は未確認。
- **UCO** — [Argus Asian UCO assessments](https://www.argusmedia.com/en/methodology/key-commodity-prices/argus-asian-uco-price-assessments)。公表/対象日：日付不明（継続更新ページ）。廃食油の地域別価格とSAF原料としての位置付け。
- **MOF** — [Japan MOF JGB information](https://www.mof.go.jp/english/policy/jgbs/)。公表/対象日：日付不明（継続更新ページ）。公式入口を確認。10年定残存利回りの日次ファイル・公表時刻は未検証。

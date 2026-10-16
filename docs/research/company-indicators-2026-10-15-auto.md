# 自動車5銘柄の企業別指標調査（2026-10-15）

企業の事業構造から、トヨタ9群・ホンダ10群・日産9群・スズキ8群・SUBARU8群を提案する。固定するのは経済的な指標群であり、係数は銘柄ごとに毎朝再推定する。自社株テクニカルや別ラグを群数の穴埋めにしない。

**公式IRで確認した事業仮説と、日中リターンの予測能力は別である。** 本調査は予測能力・最適性・改善率を検証していない。新規指標の運用取得も未実施。足りない必須群を既存の共通マクロへ差し替えず、揃うまで新プロファイルを `INSUFFICIENT_DATA` とする。

「VERIFIED」は公式提供者資料で商品/系列の存在を確認したという限定的な意味。API、履歴の完全性、08:30までの実受信、利用権は別途確認が必要。既存は `config/indicators.yaml` のIDを維持し、新規Yahooシンボルを推測していない。詳細・機械可読情報は同名JSONに記録した。

## 7203 トヨタ自動車 — 9群

地域分散した自動車・HEVの商品力と販売金融を分離する。輸出FX、地域需要、無リスク金利、信用スプレッド、燃料と鋼材を別の経済経路として持つ。

- FY2026 20-Fは米国金融子会社TMCCによる小売/リース/卸売金融と社債・CP等による調達を記載。
- 欧州を含む地域販売、HEV/BEVのマルチパスウェイ、鋼材・非鉄・燃料価格のリスクを開示。

根拠: [Toyota FY2026 Form 20-F（2026-06-10）](https://global.toyota/pages/global_toyota/ir/library/sec/20-F_202603_final.pdf)、[Toyota Integrated Report 2025（2026-02-27）](https://global.toyota/pages/global_toyota/ir/library/annual/2025_001_integrated_en.pdf)。

| ID / 指標 | 事業との関係（仮説） | 想定符号・条件 | 取得状態 |
|---|---|---|---|
| `nikkei225_futures` / 日本株市場・翌朝の市場環境 | 国内上場株の共通リスクを取り除き、事業因子の追加寄与を見る。 | 通常＋、日中は寄り付きへの織込み次第 | 既存 |
| `usdjpy` / ドル円（円/米ドル） | 円安は日本輸出の採算・米国利益の円換算を押し上げ得る。現地生産とヘッジで変わる。 | 円/ドル上昇は原則＋、固定しない | 既存 |
| `eurjpy` / ユーロ円（円/ユーロ） | 欧州の販売/金融利益と日本からの輸出採算を表す。USD一本では欧州通貨変動を落とす。 | 円/ユーロ上昇は原則＋、現地費用で相殺 | 既存 |
| `fxi` / 中国景況・販売環境 | 中国自動車市場・JV競争環境に対する広い景況代理。中国固有の弱さを米株指数と分ける。 | 景況改善は＋、中国競合優位なら逆もある | 既存・代理 |
| `xly` / 米国家計の耐久財購買環境 | 北米の家計が高額耐久財を購入する余力を表す市場代理。 | 通常＋、ETFの非自動車構成が限界 | 新規取得必須・代理 |
| `us_2y_yield` / 米国短中期金利・ローン条件 | 車両ローン負担・販売奨励金と販売金融の調達費用の金利成分。 | 金利上昇は通常−、利ざや/景気で条件付き | 既存・代理 |
| `us_ig_credit_spread` / 米国投資適格信用スプレッド | TMCC等の資本市場調達の無リスク金利以外の信用環境。 | 拡大は通常−、自社スプレッドではない | 新規取得必須・代理 |
| `rbob_gasoline` / 米国ガソリン価格・燃費選好 | 燃料高は新車需要に逆風だがHEVの相対魅力を高めるため、原油一本の負符号を強制しない。 | 総需要− / HEVミックス＋の競合 | 新規取得必須・代理 |
| `us_hrc_steel` / 北米自動車用鋼材コストの代理 | 北米製造の車体材料・サプライヤー費用の圧力。 | 上昇は通常−、価格転嫁・契約遅延で変化 | 新規取得必須・代理 |

- Toyota ADR(TM)は候補保留。既存設定で無効化済み。為替換算/ADR比率を調整した寄り前価格差は主にギャップを説明し得るため日中能力と区別。
- 9群の全てを毎朝再推定。符号の事前固定・予測精度向上の保証はしない。

## 7267 本田技研工業 — 10群

北米四輪・販売金融に加えて、インド/東南アジア/ブラジル二輪を独立に測る。二輪の収益源を米国自動車の共通リストへ埋没させない。

- 2026-08-05資料は四輪・二輪・金融を別セグメントとして示し、二輪利益の伸びをインドとブラジルに結びつける。
- 四輪は北米増販と中国での苦戦を開示。前年事業報告はインドネシアを主要二輪市場に含める。

根拠: [Honda FYE2027 Q1 presentation（2026-08-05）](https://global.honda/en/investors/library/financialresult/main/010/teaserItems3/015/linkList/00/link/FYE202703_1Q_financial_presentation_e_1.pdf)、[Honda 102nd fiscal year business report（2026-06）](https://global.honda/en/investors/library/filings/auto_20260602560203/pdfFile.pdf)。

| ID / 指標 | 事業との関係（仮説） | 想定符号・条件 | 取得状態 |
|---|---|---|---|
| `nikkei225_futures` / 日本株市場・翌朝の市場環境 | 国内上場株の市場共通成分。 | 通常＋ | 既存 |
| `usdjpy` / ドル円（円/米ドル） | 北米四輪/金融の円換算、日本輸出と現地コストの相対価値。 | 原則＋、ヘッジ次第 | 既存 |
| `xly` / 米国家計の耐久財購買環境 | 北米四輪需要の市場代理。二輪の地域需要とは区別する。 | 通常＋、代理誤差あり | 新規取得必須・代理 |
| `us_2y_yield` / 米国短中期金利・ローン条件 | 北米顧客のローン負担と金融子会社の金利調達費用。 | 上昇は通常−、利ざや次第 | 既存・代理 |
| `us_ig_credit_spread` / 米国投資適格信用スプレッド | 自動車販売を支える小売金融/リースの信用・資金調達条件。 | 拡大は通常− | 新規取得必須・代理 |
| `inda` / インド家計・景況環境 | インド二輪顧客の所得・消費環境に対する市場代理。 | 通常＋、二輪実売の直接測定ではない | 新規取得必須・代理 |
| `eido` / インドネシア二輪需要の景況代理 | 大きな二輪市場であるインドネシア固有の内需と金融環境。 | 通常＋、銀行/資源株偏重に注意 | 新規取得必須・代理 |
| `brljpy` / ブラジルレアル円（円/BRL） | ブラジル二輪事業の利益換算・通貨/輸入部材条件。 | BRL高の換算＋と費用/需要効果が競合 | 新規取得必須 |
| `fxi` / 中国景況・販売環境 | 中国四輪需要・競争環境を北米と切り分ける。 | 景況＋と競合販売増−が競合 | 既存・代理 |
| `rbob_gasoline` / 米国ガソリン価格・燃費選好 | 北米四輪の保有コスト・ハイブリッド選好。二輪の燃費優位を同じ固定係数で決め付けない。 | 条件付き | 新規取得必須・代理 |

- Honda ADR(HMC)は既存設定で無効化済みのため固定10群に数えない。再採用は独立した寄り付きギャップ対日中の検証が必要。
- INDA/EIDOは二輪販売の代理として弱い。月次国別二輪販売と突合し、無効なら根拠付きプロファイル改訂を行う。

## 7201 日産自動車 — 9群

再建・流動性制約の下で、米国/メキシコ生産販売と中国競争を分ける。為替だけでなく、信用環境と値引きが必要になる需要環境を扱う。

- FY2025台数資料は米国、中国、メキシコを含む北米の重要性を示す。
- 株主総会資料はRe:Nissanのコスト削減、米関税への生産/物流対応、負の自動車フリーキャッシュフローを記載。

根拠: [Nissan 127th shareholders meeting business report（2026-06）](https://www.nissan-global.com/EN/IR/STOCK/SHAREHOLDER/127/ASSETS/PDF/shousyu2026.pdf)、[Nissan FY2025 global volume information（2026-05-13）](https://www.nissan-global.com/EN/IR/FINANCIAL_RESULTS/ASSETS/DATA/2025/20254th_summary_863_e.pdf)。

| ID / 指標 | 事業との関係（仮説） | 想定符号・条件 | 取得状態 |
|---|---|---|---|
| `nikkei225_futures` / 日本株市場・翌朝の市場環境 | 国内株式の共通市場要因。 | 通常＋ | 既存 |
| `usdjpy` / ドル円（円/米ドル） | 米ドル売上・日本輸出と利益換算。 | 原則＋、現地生産/調達を考慮 | 既存 |
| `mxnjpy` / メキシコペソ円（円/MXN） | メキシコ生産のコストと現地販売利益の換算。 | MXN高は輸出採算−と換算＋が競合 | 新規取得必須 |
| `fxi` / 中国景況・販売環境 | 中国市場の需要・現地競争圧力を表す広い代理。 | 需要回復＋、競合優位なら逆 | 既存・代理 |
| `xly` / 米国家計の耐久財購買環境 | 北米顧客の購買余力・販売奨励金必要性。 | 通常＋、日産固有シェア低下は別途確認 | 新規取得必須・代理 |
| `us_2y_yield` / 米国短中期金利・ローン条件 | ローン負担と販売金融の無リスク資金調達金利。 | 上昇は通常− | 既存・代理 |
| `us_hy_credit_spread` / 米国ハイイールド信用スプレッド | 再建中の資金調達環境/市場の信用リスク許容度を広い信用指数で表す。 | 拡大は通常−。日産格付や個社OASの代用値とは呼ばない | 新規取得必須・代理 |
| `rbob_gasoline` / 米国ガソリン価格・燃費選好 | 米国の車両保有費・ICE/SUVと省燃費車の選好。 | 通常需要−、車種ミックスで変化 | 新規取得必須・代理 |
| `us_hrc_steel` / 北米自動車用鋼材コストの代理 | 北米生産・部品調達の鋼材コスト。再建コスト削減と区別。 | 通常−、関税/転嫁/調達契約で条件付き | 新規取得必須・代理 |

- 市場系列は工場閉鎖・資産売却・提携・新車不振そのものを観測しない。公式開示/イベント時刻の取得を別途要求し、金融リスクの説明を補う。
- メキシコFXは輸出費用と現地需要の両面があり負符号を固定しない。

## 7269 スズキ — 8群

日本小型車とインドのMarutiを中心に、ルピー、現地金利、子会社の固有評価、欧州、輸入燃料・軽量材料を測る。米国自動車比重の高い他社と同じ指標にしない。

- FY2026 Q1資料はインドを大きな地域別収益源として示し、北米自動車収益は部品・用品と注記する。
- インド投資とCNG/バイオガスへの取り組みが示され、2026-09-29の月次発表もインドの販売・生産を個別開示する。

根拠: [Suzuki FY2026 Q1 presentation（2026-08-05）](https://www.globalsuzuki.com/ir/library/financialresults/pdf/2026/financial_presentation_1q.pdf)、[Suzuki India business presentation, CNG economics p.34（2026-03-17）](https://www.globalsuzuki.com/ir/library/financialpresentation/pdf/2026/ibp_20260317.pdf)、[Suzuki August 2026 production and sales（2026-09-29）](https://www.globalsuzuki.com/globalnews/2026/0929.html)。

| ID / 指標 | 事業との関係（仮説） | 想定符号・条件 | 取得状態 |
|---|---|---|---|
| `nikkei225_futures` / 日本株市場・翌朝の市場環境 | 国内小型車事業と上場株式市場の共通環境。 | 通常＋ | 既存 |
| `inrjpy` / インドルピー円（円/INR） | インド事業利益の円換算、輸入部材コストとルピー購買力。 | INR高の換算は＋、輸出/調達との条件付き | 新規取得必須 |
| `maruti_suzuki` / 主要子会社Maruti固有の市場評価 | 上場主要子会社に固有の販売/新車/設備投資/競争に対する情報。 | 通常＋、親子上場評価差は変動 | 新規取得必須 |
| `inda` / インド家計・景況環境 | Maruti以外も含むインド家計・設備投資の景況。子会社固有情報と区別する。 | 通常＋。Marutiとの共線性を検証 | 新規取得必須・代理 |
| `india_10y_yield` / インド自動車ローン・設備投資の金利環境 | 小型車の購買ローン条件・現地増産投資の資本コスト。 | 通常−、景気上昇要因なら混合 | 新規取得必須・代理 |
| `eurjpy` / ユーロ円（円/ユーロ） | 欧州自動車/二輪販売の換算・日本輸出採算。 | 通常＋、欧州現地費用で相殺 | 既存 |
| `brent` / 輸入燃料・石油化学コスト | インドの輸入燃料・石油化学原料・物流費と消費者負担。 | 通常−、低燃費/CNGへの需要シフトは＋ | 既存・代理 |
| `lme_aluminium` / 軽量化部材・アルミ調達コスト | 小型軽量車の材料・部品原価の広い代理。 | 通常−、購買契約と価格転嫁で遅れる | 新規取得必須・代理 |

- 米国自動車需要や米長短金利を7群確保のために足さない。MarutiとINDAは同じ国の2指数ではなく子会社固有情報と市場環境だが、重複寄与の監査は必要。
- CNG相対燃料費は重要な別候補だが価格改定が不定期で日次変動が乏しいため固定8群に数えず補助状態/イベントとして別途取得する。

## 7270 SUBARU — 8群

北米に集中するICE/HEV・SUV事業の需要、消費者金融、燃料、鋼材、触媒貴金属を優先する。中国やインドの指標を数合わせで含めない。

- FYE2026参考表では北米販売708千台、全世界896千台で北米約79%。
- 最新Q1説明は販売奨励金・原材料費、米関税、貴金属市況、ICE/HEVと提携BEVを挙げる。

根拠: [Subaru latest results and forecast, FYE2027 Q1（2026-08-05）](https://www.subaru.co.jp/en/ir/finance/latest-results.html)、[Subaru FYE2026 results reference（2026-05-15）](https://www.subaru.co.jp/en/ir/library/pdf/fr/2026/2026_4qf_rm_e.pdf)、[Subaru FYE2026 analyst Q&A（2026-05-15）](https://www.subaru.co.jp/en/ir/library/pdf/fr/2026/2026_4qf_qa_e.pdf)。

| ID / 指標 | 事業との関係（仮説） | 想定符号・条件 | 取得状態 |
|---|---|---|---|
| `nikkei225_futures` / 日本株市場・翌朝の市場環境 | 国内上場市場の共通変動。 | 通常＋ | 既存 |
| `usdjpy` / ドル円（円/米ドル） | 日本生産から北米への輸出と米国利益換算。 | 原則＋、米国現地生産が相殺 | 既存 |
| `xly` / 米国家計の耐久財購買環境 | 主要米国顧客の高額耐久財購入余力・奨励金圧力。 | 通常＋、SUV需要の直接測定ではない | 新規取得必須・代理 |
| `us_2y_yield` / 米国短中期金利・ローン条件 | 米国顧客のローン/リース負担と販売支援費。 | 上昇は通常− | 既存・代理 |
| `us_ig_credit_spread` / 米国投資適格信用スプレッド | 金利とは独立した米国消費金融・信用供給の市場代理。 | 拡大は通常−、個社販売金融の実測ではない | 新規取得必須・代理 |
| `rbob_gasoline` / 米国ガソリン価格・燃費選好 | 北米SUVの維持費とICEからHEVへの商品選好。 | 総需要− / HEV選好＋の競合 | 新規取得必須・代理 |
| `us_hrc_steel` / 北米自動車用鋼材コストの代理 | 米国現地生産・部品仕入の鋼材価格圧力。 | 通常−、長期契約で遅延 | 新規取得必須・代理 |
| `palladium` / 触媒用貴金属コストの代理 | 会社が指摘する貴金属費用を触媒用途の代表価格で追う。 | 通常−、PGM組成・ヘッジ・代替で変化 | 新規取得必須・代理 |

- 航空宇宙事業を存在するだけの理由で独立株指数1群にしない。規模・日次感応度の追加証拠なし。
- 貴金属全体をパラジウム一つで完全再現はできない。Pt/Pd購買比率・ヘッジの確認を依頼。

## 日次取得と08:30締切

| ID | 公式提供者・商品 | 頻度と締切 | 限界 |
|---|---|---|---|
| `nikkei225_futures` | CME Nikkei 225 JPY futures NIY。既存Yahoo NIY=Fは既存設定の対応を継続。 / [CME Japanese equity index futures product card](https://www.cmegroup.com/markets/equities/files/trade-japanese-equity-index-futures-fact-card.pdf) | 日次。前の完了した取引日の確定清算値を使用。限月・ロールと公表時刻を保存。08:30時点の進行中日足や事後更新値は禁止。 | 共通市場因子は原則この1群。S&P先物/Nasdaq/VIXを数合わせで追加しない。 |
| `usdjpy` | ECB EUR/JPY と EUR/USD の参照値。JPY per USD = JPY per EUR / USD per EUR / [ECB euro reference exchange rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html) | 日次。ECBの同一公表日のEUR建て参照値からクロス算出。欧州営業日の16:00頃公表後、08:30 JSTまでに実受信した値のみ。取引可能なスポット値とは区別。 | 現行USD/JPY・EUR/JPY系列とECB系列はフィキシング時刻が異なるため無記録に混在させない。 |
| `eurjpy` | ECB EUR/JPY reference rate / [ECB euro reference exchange rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html) | 日次。ECBの同一公表日のEUR建て参照値からクロス算出。欧州営業日の16:00頃公表後、08:30 JSTまでに実受信した値のみ。取引可能なスポット値とは区別。 | 現行USD/JPY・EUR/JPY系列とECB系列はフィキシング時刻が異なるため無記録に混在させない。 |
| `fxi` | 米国上場ETF FXI の調整済み日次終値 / [iShares China Large-Cap ETF FXI](https://www.ishares.com/us/products/239536/ishares-china-largecap-etf?periodCd=d) | 日次。前の完了した米国通常取引日の終値。米16:00=日本翌05:00/06:00が目安。終値確定・実受信を08:30までに確認。 | 国/消費セクター株の代理であり当該企業の販売台数ではない。構成銘柄偏りと通貨・米株市場要因を併せて評価。 |
| `xly` | 米国上場ETF XLY の調整済み日次終値 / [State Street Consumer Discretionary Select Sector SPDR ETF XLY](https://www.ssga.com/us/en/individual/etfs/state-street-consumer-discretionary-select-sector-spdr-etf-xly) | 日次。前の完了した米国通常取引日の終値。米16:00=日本翌05:00/06:00が目安。終値確定・実受信を08:30までに確認。 | 国/消費セクター株の代理であり当該企業の販売台数ではない。構成銘柄偏りと通貨・米株市場要因を併せて評価。 |
| `us_2y_yield` | US Treasury Daily Par Yield Curve 2-year / [US Treasury interest-rate statistics and methodology](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics) | 日次。米国市場観測時刻は概ね15:30 ET。観測時刻を公表時刻と見なさず、公式配信の実受信が08:30以前かを確認。 | 車両ローン金利そのものではない。水準・前年差ではなく日次bp変化を主特徴にする。 |
| `us_ig_credit_spread` | FRED/ICE BofA OAS BAMLC0A0CM 日次終値。変化はbp単位 / [FRED ICE BofA US Corporate OAS BAMLC0A0CM](https://fred.stlouisfed.org/series/BAMLC0A0CM) | 日次。観測日ではなくFRED公表/実受信日で利用判定。確認例:10/13観測を10/14 10:26 CDTに更新。翌朝に前々米営業日の値しかない場合がある。 | 個社信用スプレッドや自動車ローンABSの直接データではない。2026年4月から公開履歴は3年に制限。権利条件と過去版を確認。 |
| `rbob_gasoline` | CME NYMEX RBOB gasoline futures RB / [CME RBOB gasoline futures contract specifications](https://www.cmegroup.com/markets/energy/refined-products/rbob-gasoline.contractSpecs.html) | 日次。前の完了した取引日の確定清算値を使用。限月・ロールと公表時刻を保存。08:30時点の進行中日足や事後更新値は禁止。 | 消費者小売価格ではなく卸売先物。季節性・精製マージン・ロールの影響。原油WTI/Brentで無断代替しない。 |
| `us_hrc_steel` | CME US Midwest Domestic Hot-Rolled Coil Steel (CRU) Index futures HRC / [CME US Midwest HRC steel futures contract specifications](https://www.cmegroup.com/markets/metals/ferrous/hrc-steel.contractSpecs.html) / [CME HRC steel product card](https://www.cmegroup.com/trading/metals/files/us-midwest-domestic-hot-rolled-coil-steel-futures-and-options.pdf) | 日次。前の完了した取引日の確定清算値を使用。限月・ロールと公表時刻を保存。08:30時点の進行中日足や事後更新値は禁止。 | 取引の薄い限月の停止価格に注意。各社契約調達価格でもアジア鋼材価格でもない。CME/許諾ベンダーの履歴取得未実装。 |
| `inda` | 米国上場ETF INDA の調整済み日次終値 / [iShares MSCI India ETF INDA](https://www.ishares.com/us/products/239659/ishares-msci-india-etf?periodCd=m) | 日次。前の完了した米国通常取引日の終値。米16:00=日本翌05:00/06:00が目安。終値確定・実受信を08:30までに確認。 | 国/消費セクター株の代理であり当該企業の販売台数ではない。構成銘柄偏りと通貨・米株市場要因を併せて評価。 |
| `eido` | 米国上場ETF EIDO の調整済み日次終値 / [iShares MSCI Indonesia ETF EIDO](https://www.ishares.com/us/products/239661/ishares-msci-indonesia-etf) | 日次。前の完了した米国通常取引日の終値。米16:00=日本翌05:00/06:00が目安。終値確定・実受信を08:30までに確認。 | 国/消費セクター株の代理であり当該企業の販売台数ではない。構成銘柄偏りと通貨・米株市場要因を併せて評価。 |
| `brljpy` | ECB EUR/JPY と EUR/BRL の参照値。JPY per BRL = JPY per EUR / BRL per EUR / [ECB euro reference exchange rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html) | 日次。ECBの同一公表日のEUR建て参照値からクロス算出。欧州営業日の16:00頃公表後、08:30 JSTまでに実受信した値のみ。取引可能なスポット値とは区別。 | 現行USD/JPY・EUR/JPY系列とECB系列はフィキシング時刻が異なるため無記録に混在させない。 |
| `mxnjpy` | ECB EUR/JPY と EUR/MXN の参照値。JPY per MXN = JPY per EUR / MXN per EUR / [ECB euro reference exchange rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html) | 日次。ECBの同一公表日のEUR建て参照値からクロス算出。欧州営業日の16:00頃公表後、08:30 JSTまでに実受信した値のみ。取引可能なスポット値とは区別。 | 現行USD/JPY・EUR/JPY系列とECB系列はフィキシング時刻が異なるため無記録に混在させない。 |
| `us_hy_credit_spread` | FRED/ICE BofA OAS BAMLH0A0HYM2 日次終値。変化はbp単位 / [FRED ICE BofA US High Yield OAS BAMLH0A0HYM2](https://fred.stlouisfed.org/series/BAMLH0A0HYM2) | 日次。観測日ではなくFRED公表/実受信日で利用判定。確認例:10/13観測を10/14 10:26 CDTに更新。翌朝に前々米営業日の値しかない場合がある。 | 個社信用スプレッドや自動車ローンABSの直接データではない。2026年4月から公開履歴は3年に制限。権利条件と過去版を確認。 |
| `inrjpy` | ECB EUR/JPY と EUR/INR の参照値。JPY per INR = JPY per EUR / INR per EUR / [ECB euro reference exchange rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html) | 日次。ECBの同一公表日のEUR建て参照値からクロス算出。欧州営業日の16:00頃公表後、08:30 JSTまでに実受信した値のみ。取引可能なスポット値とは区別。 | 現行USD/JPY・EUR/JPY系列とECB系列はフィキシング時刻が異なるため無記録に混在させない。 |
| `maruti_suzuki` | NSE equity MARUTI 調整済み日次終値 / [NSE Maruti Suzuki equity quote MARUTI](https://www.nseindia.com/get-quote/equity/MARUTI/Maruti-Suzuki-India-Limited) | 日次。前の完了したインド通常取引日の確定終値。取引所カレンダー、配当/分割、受信時刻を保存。 | 親会社自社株テクニカルではなくインド上場子会社情報。INDAとの重複は係数安定性で監査。必要なら過去窓のみで市場成分を分離。 |
| `india_10y_yield` | CCIL Daily Market Analytics G-Sec 10-Year yield（benchmark債とconstant maturityの定義を固定） / [CCIL Daily Market Analytics: 10-year G-Sec yield](https://www.ccilindia.com/documents/43866/111510/CCIL_DAILY_ANALYSIS_31_12_25.pdf/3ce30f58-5aa4-26e0-043d-62e45bd64c34?download=true&t=1767186871028&version=1.0) | 日次。日次公表の実時刻を確認して08:30以前の最新値を使用。参考資料で日次表の存在のみ確認し、当日公開時刻/最新APIは未検証。 | 車両ローン金利の代理。日次履歴の定義を固定し、ベンチマーク交代ジャンプを処理。 |
| `brent` | CME NYMEX Brent Oil Last Day futures BZ。現行Brent系列を使用。 / [CME energy futures bulletin: BZ Brent oil](https://www.cmegroup.com/daily_bulletin/current/Section61_Energy_Futures_Products.pdf) | 日次。前の完了した取引日の確定清算値を使用。限月・ロールと公表時刻を保存。08:30時点の進行中日足や事後更新値は禁止。 | インド現地CNG・ガソリン小売価格とは異なる。 |
| `lme_aluminium` | LME Aluminium Official Cash Settlement USD/t / [LME Official Prices](https://www.lme.com/Market-data/Reports-and-data/LME-Official-Prices) / [LME reports and data: day-delayed public prices](https://www.lme.com/en/Market-data/Reports-and-data) | 日次。LME公表は概ね12:20–13:25 London。公開画面はday-delayedであり、当日清算値と同時利用不可。配信経路ごとの公表時刻と遅延を保存。 | スズキがアルミ単独感応度を公表したという意味ではない。原材料費/軽量車の機構からの仮説。権利・履歴取得未確認。 |
| `palladium` | CME NYMEX palladium futures PA / [CME palladium futures contract specifications](https://www.cmegroup.com/markets/metals/precious/palladium.contractSpecs.html) / [CME SER-8668 identifying PA futures](https://www.cmegroup.com/content/dam/cmegroup/notices/ser/2020/10/SER-8668.pdf) | 日次。前の完了した取引日の確定清算値を使用。限月・ロールと公表時刻を保存。08:30時点の進行中日足や事後更新値は禁止。 | SUBARU資料は貴金属市況を特定するがパラジウム単独感応度は非開示。プラチナ代替・契約ヘッジで符号/遅延は変わる。 |

FREDの信用スプレッドは2026-10-13観測値が10-14 10:26 CDTに更新されている。観測日だけで前日の日本朝に利用可能だったと判断してはいけない。LMEの公開ページはday-delayedである。ECBクロスは同じ公表日のJPY/各通貨を使い、現在のスポットFXと黙って継ぎ足さない。

## 追加取得依頼

- **inrjpy**（対象 7269、必須）: ECB EUR/JPY と EUR/INR の参照値。JPY per INR = JPY per EUR / INR per EUR の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **brljpy**（対象 7267、必須）: ECB EUR/JPY と EUR/BRL の参照値。JPY per BRL = JPY per EUR / BRL per EUR の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **mxnjpy**（対象 7201、必須）: ECB EUR/JPY と EUR/MXN の参照値。JPY per MXN = JPY per EUR / MXN per EUR の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **inda**（対象 7267, 7269、必須）: 米国上場ETF INDA の調整済み日次終値 の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **eido**（対象 7267、必須）: 米国上場ETF EIDO の調整済み日次終値 の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **xly**（対象 7203, 7267, 7201, 7270、必須）: 米国上場ETF XLY の調整済み日次終値 の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **us_ig_credit_spread**（対象 7203, 7267, 7270、必須）: FRED/ICE BofA OAS BAMLC0A0CM 日次終値。変化はbp単位 の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **us_hy_credit_spread**（対象 7201、必須）: FRED/ICE BofA OAS BAMLH0A0HYM2 日次終値。変化はbp単位 の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **rbob_gasoline**（対象 7203, 7267, 7201, 7270、必須）: CME NYMEX RBOB gasoline futures RB の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **us_hrc_steel**（対象 7203, 7201, 7270、必須）: CME US Midwest Domestic Hot-Rolled Coil Steel (CRU) Index futures HRC の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **palladium**（対象 7270、必須）: CME NYMEX palladium futures PA の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **lme_aluminium**（対象 7269、必須）: LME Aluminium Official Cash Settlement USD/t の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **maruti_suzuki**（対象 7269、必須）: NSE equity MARUTI 調整済み日次終値 の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **india_10y_yield**（対象 7269、必須）: CCIL Daily Market Analytics G-Sec 10-Year yield（benchmark債とconstant maturityの定義を固定） の日次履歴と時点情報を取得。最低120学習営業日＋特徴量ウォームアップ20営業日、さらに未使用評価期間を確保。
- **india_cng_affordability**（対象 7269、補助・固定群に含めない）: IGL Delhi CNG/kgとIndianOil Delhi petrol/litreの改定履歴・公表時刻。Suzukiが示す車種別燃費を固定した相対保有費、または基準日を固定した価格指数比を作る。
  IndianOilは06:00 IST=09:30 JSTに当日価格改定。08:30予測で当日改定値を使わず前日まで。将来日付のCNG価格は公表時刻を分ける。 価格改定が疎であるため、データを毎日取得することと毎日変動する説明変数であることを区別する。根拠: [Suzuki India business presentation, CNG economics p.34](https://www.globalsuzuki.com/ir/library/financialpresentation/pdf/2026/ibp_20260317.pdf)、[Indraprastha Gas CNG price schedule](https://www.iglonline.net//cng-png-prices)、[IndianOil petrol and diesel daily pricing](https://iocl.com/petrol-diesel-price)。
- **auto_company_events**（対象 7203, 7267, 7201, 7269, 7270、補助・固定群に含めない）: 会社公式の国別月次販売/生産、HEV/CNG比率、新車、工場停止、関税影響、再建、金融損失等を発表時刻付きで保存。月次/四半期数字を日次観測と偽装しない。

全新規系列について、最低120学習営業日＋20日のウォームアップと独立した評価期間、観測日・公表時刻・実受信時刻・原データハッシュ、通貨単位、休日、価格改訂、配当分割/先物ロールを揃える。取得権限・価格は調査段階であり、購入・アカウント作成・DB操作は行っていない。

## 推定・検証の運用

各群の代表的な日次変化1列を基本にする（金利/信用はbp、FX/株価/商品はリターン）。数十個のラグを追加して係数数を水増ししない。標準化と補完は各学習窓内で実施し、すべての群を維持したまま銘柄別に係数を更新する。事業上の想定符号は検証の観点であり、学習係数を強制しない。

新プロファイルは2026-10-15以降の方針。過去期間に当時知られていなかった事業判断を遡及適用しない。旧セットとの同期間ウォークフォワード比較は、銘柄別MAE/方向性/確率校正に加えてギャップと日中を分けて行う。新しい分布予測の被覆率に旧モデルの検証結果を流用しない。

Toyota/Honda ADRは存在するが既存設定では無効。為替とADR比率を整合させた価格差でも主に寄り付きギャップの情報になり得る。旧測定結果の再現や新たな独立OOS証拠なしに固定群へ戻さない。

参照した資料は2026-10-15以前に公表されたものに限定。日付が明示されない商品ページは公表日不明・閲覧日2026-10-15としてJSONに記録した。

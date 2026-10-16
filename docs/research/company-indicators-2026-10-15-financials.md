# 金融5社の企業別固定指標案（2026-10-15）

金融5社・事業根拠に基づく固定7〜10指標群の研究仕様。未配備。

JPX当日のraw close/open - 1。毎朝08:30 JSTまでの情報で係数を推定。

## 選び方と検証範囲

派生return/volatility/lag/levelは同じ原系列の1群。金利の年限や差分で指標数を水増ししない。会社ごとに本書の固定集合を使い係数のみ毎朝推定。

事業関連性は公式IRで確認。入力の機構/符号は分析者の仮説。新指標を用いた株式バックテストもAPI履歴取得試験も本調査では未実施。

既存verifiedはrepo config上の確認を引継ぐだけで、本日再測定済みではない。新規のinstrument/source存在確認は、provider symbolや08:30に利用可能な履歴の実測確認と別。未取得は共通マクロで穴埋めせずINSUFFICIENT_DATA。

2026-10-15時点に判明した事業構造の仮説。過去OOSへ同じ事業構成を遡及しない。過去再現には当時公表されたIR/出資完了日/配信データを用いたプロファイル版が必要。

**重要な公表ラグ:** 財務省JGB金利は観測翌営業日09:30公表のため、08:30予測で前日値は使えない。通常はt-2以前の最新公表値になる。[財務省公式Q&A](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/qa.htm)。FRED OASも観測日の米国引け時点に利用可能とは限らない。[FRED定義・更新日時](https://fred.stlouisfed.org/series/BAMLC0A0CM)。

## 8306 三菱UFJフィナンシャル・グループ — 9群

国内の預貸・預金収益に、米ドル業務、Morgan Stanleyとの収益連携、タイ/インドネシア銀行事業を分ける。金利3年限とFXだけの共通セットではこの違いを表せない。

公式事業根拠: [MUFG Investors Day 2026（2026-09-09）](https://www.mufg.jp/dam/ir/presentation/2026/pdf/slides260901_en.pdf), [MUFG FY2026 IR資料一覧（2026-10-02）](https://www.mufg.jp/english/ir/presentation/2026/index.html)。下表の採用理由・符号はその事業構造からの推論。

| ID / 指標 | 収益への経路 | 符号の条件 |
|---|---|---|
| `jgb_2y_yield` 日本2年国債利回り | 円の預貸再価格付け・預金基盤の収益環境。短めのJGBは政策金利期待の代理。 | 上昇は利鞘改善に寄与し得るが預金追随、貸倒、債券評価損により逆転。 |
| `us_2y_yield` 米2年国債利回り | ドル貸出/調達の再価格付け環境。円金利とは別通貨の営業収益。 | 上昇時の資産金利と調達費用のどちらが速いかで符号不定。 |
| `usdjpy` ドル円 | 海外利益・資本の円換算と為替業務。 | 円安は換算益要因だがヘッジ費用/RWAや顧客信用で相殺。 |
| `nikkei225_futures` 日本株市場（既存日経225先物代理） | 国内顧客資産価値、証券/WM手数料、保有株式評価の市場代理。 | 上昇は通常追い風。銀行株構成比が小さく完全な資産ポートフォリオではない。 |
| `us_ig_credit_oas` 米投資適格社債信用スプレッド | 海外企業貸出/債券・資本市場の信用環境。 | 拡大は信用損失・評価損懸念。貸出スプレッド改善との純効果は条件依存。 |
| `morgan_stanley_equity` Morgan Stanley株価（出資・協業先） | MSの証券・資産運用業績期待と協業価値を米銀行一般から区別。 | 同方向仮説。MS株価は持分利益そのものではなく市場リスクも含む。 |
| `krungsri_equity` タイKrungsri株価 | タイの個人・自動車金融/銀行業の子会社収益期待。 | 同方向仮説。現地金利・貸倒と薄商いにより感応度が変わる。 |
| `danamon_equity` インドネシアDanamon株価 | インドネシア銀行・自動車金融事業の子会社収益期待。 | 同方向仮説。タイとは貸出/信用サイクルが異なる。 |
| `usd_jpy_cross_currency_basis` 円ドル通貨ベーシス（外貨調達費用） | 円調達をドルへ交換する際の追加資金費用。金利差そのものではない。 | ドル調達プレミアム増は逆風。符号規約を正の調達費用増に正規化。 |

**現行設定で取得経路がある採用群:** `us_2y_yield`, `usdjpy`, `nikkei225_futures`。

**追加取得が必要:** `jgb_2y_yield`, `us_ig_credit_oas`, `morgan_stanley_equity`, `krungsri_equity`, `danamon_equity`, `usd_jpy_cross_currency_basis`。

INSUFFICIENT_DATA：追加取得/時点整合検証を終えるまで完全プロファイルとして予測しない。

## 8316 三井住友フィナンシャルグループ — 8群

国内預貸に加えOlive/カード決済、Jefferies協業、YES BANKとインド消費者金融を分離する。決済取扱高は日次契約データが必要で、米金融ETFで補わない。

公式事業根拠: [SMBC Group IR Day 2026（2026-08-26）](https://www.smfg.co.jp/english/investor/financial/small/pdf/20260826irday_pre01e.pdf), [SMFG Investor Meeting FY3/2026（2026-05-18）](https://www.smfg.co.jp/english/investor/financial/latest_statement/2026_3/2026_fy_e_pre.pdf)。下表の採用理由・符号はその事業構造からの推論。

| ID / 指標 | 収益への経路 | 符号の条件 |
|---|---|---|
| `jgb_2y_yield` 日本2年国債利回り | 国内預金/貸出の再価格付けとOliveで拡充する預金の収益化。 | 上昇は利鞘に追い風だが調達競争/信用費用で逆転。 |
| `us_2y_yield` 米2年国債利回り | グローバル法人融資のドル資産・負債利回り。 | 資産再価格付けと預金金利の追随速度による。 |
| `usdjpy` ドル円 | 海外融資・持分利益の円換算、顧客為替取引。 | 円安換算益と資本/ヘッジ費用は相殺し得る。 |
| `nikkei225_futures` 日本株市場（既存日経225先物代理） | 国内WM資産残高と顧客の資本取引意欲。 | 通常同方向だが価格上昇だけでは取引手数料増を保証しない。 |
| `us_ig_credit_oas` 米投資適格社債信用スプレッド | 海外貸出信用費用と引受/資金調達市場。 | 拡大は一般に逆風、貸出収益率改善は遅れて発現。 |
| `jefferies_equity` Jefferies株価（出資・協業先） | 出資/協業先のIB・S&T期待を反映。 | 通常同方向だがJEF固有評価とSMFGの連結収益は一致しない。 |
| `india_bank_index` インド銀行株指数 | YES BANK/SMFG India Creditに関係するインド信用需要・銀行株評価。 | 上昇は追い風仮説。指数の構成は投資先そのものと異なる。 |
| `japan_card_transaction_value` 国内カード決済取扱高 | SMCC/Olive決済取扱高から加盟店・決済手数料へ。 | 取扱高増は通常プラス。料率、キャンペーン費用、貸倒率で収益化が変わる。 |

**現行設定で取得経路がある採用群:** `us_2y_yield`, `usdjpy`, `nikkei225_futures`。

**追加取得が必要:** `jgb_2y_yield`, `us_ig_credit_oas`, `jefferies_equity`, `india_bank_index`, `japan_card_transaction_value`。

INSUFFICIENT_DATA：追加取得/時点整合検証を終えるまで完全プロファイルとして予測しない。

## 8411 みずほフィナンシャルグループ — 8群

国内預貸だけでなく、米州FICC/DCMとMizuho-Greenhillを含むグローバルCIB、国内証券顧客の売買活動、ドル調達費用を区別する。

公式事業根拠: [MIZUHO IR Day 2026（2026-09-11）](https://www.mizuho-fg.co.jp/investors/financial/briefing/pdf/20260907_1.pdf), [みずほ2025年度決算会社説明会（2026-05-19）](https://www.mizuho-fg.co.jp/investors/financial/briefing/pdf/20260519_1.pdf)。下表の採用理由・符号はその事業構造からの推論。

| ID / 指標 | 収益への経路 | 符号の条件 |
|---|---|---|
| `jgb_2y_yield` 日本2年国債利回り | 国内円建て利鞘と政策金利期待。 | 上昇利鞘効果と債券損失/信用費用の綱引き。 |
| `us_10y_yield` 米10年国債利回り | 米州Rates/FICC取引とドル債引受の価格環境。 | 水準上昇自体の符号は固定しない。取引活性化と評価損は逆向き。 |
| `usdjpy` ドル円 | 海外CIB利益の換算とFX顧客フロー。 | 換算とヘッジ/RWA負担の純効果は条件依存。 |
| `nikkei225_futures` 日本株市場（既存日経225先物代理） | 国内顧客株式資産、WM・企業資本政策需要の市場価格代理。 | 通常同方向。短期には寄り付きで織込み済みの可能性。 |
| `us_ig_credit_oas` 米投資適格社債信用スプレッド | DCM/クレジットFICCと海外企業貸出の信用価格。 | 拡大は引受/評価に逆風、取引収益増もあり得る。 |
| `us_capital_markets_etf` 米資本市場業ETF | 米投資銀行/証券業の手数料・取引環境をKCEで代理。 | 通常同方向仮説。取引所/運用業も含むため純IBベータではない。 |
| `japan_equity_turnover` 東証株式市場の日次売買代金 | 国内証券・リテールの取引量に伴うフロー収益。 | 売買代金増は手数料に追い風だが相場下落時の損失で打消し。 |
| `usd_jpy_cross_currency_basis` 円ドル通貨ベーシス（外貨調達費用） | 海外資産を支える円ドル交換による調達プレミアム。 | ドル調達コスト増は逆風、ALM/外貨預金で緩和。 |

**現行設定で取得経路がある採用群:** `us_10y_yield`, `usdjpy`, `nikkei225_futures`。

**追加取得が必要:** `jgb_2y_yield`, `us_ig_credit_oas`, `us_capital_markets_etf`, `japan_equity_turnover`, `usd_jpy_cross_currency_basis`。

INSUFFICIENT_DATA：追加取得/時点整合検証を終えるまで完全プロファイルとして予測しない。

## 8604 野村ホールディングス — 8群

銀行の預貸利鞘より、国内WM顧客資産、買収後を含む世界の運用AUM、Wholesaleの株式/信用取引、取引量と変動率に重点を置く。

公式事業根拠: [Nomura Investor Day 2026 CEO資料（2026-05-29）](https://www.nomuraholdings.com/en/investor/presentation/main/017/teaserItems1/00/tableContents/00/multiFileUpload30/link/2026_0529_01_prem.pdf), [野村 FY2026/27 1Q公式決算解説（2026-07-29）](https://plus.nomuraholdings.com/en/latest/20260729-FY202703-1Q-Highlights.html), [Macquarie米欧公開資産運用事業の買収発表（2025-04-22）](https://www.nomuraholdings.com/en/news/nr/news20250422.html)。下表の採用理由・符号はその事業構造からの推論。

| ID / 指標 | 収益への経路 | 符号の条件 |
|---|---|---|
| `nikkei225_futures` 日本株市場（既存日経225先物代理） | 国内WM顧客資産と担保価値・資産連動報酬。 | 上昇は通常プラス、顧客資産配分と利確/流出で変わる。 |
| `sp500_futures` 米国株式市場（既存S&P500先物） | 海外株式AUM・米株関連顧客事業の市場価値。 | 通常同方向。米国大型株は世界運用資産全体の不完全な代理。 |
| `global_aggregate_bond_etf` 世界投資適格債券市場（運用AUM代理） | 買収事業を含む債券AUM価値と運用報酬。 | 債券総収益改善は通常プラス。金利水準の上昇とは方向が異なり得る。 |
| `usdjpy` ドル円 | 海外収益/外貨建て顧客資産の円換算。 | 円安は円換算額を押し上げるが資金費用やヘッジで変わる。 |
| `japan_equity_turnover` 東証株式市場の日次売買代金 | 国内WM/証券執行のフロー収益。 | 増加は通常プラス。ただし顧客構成・単価・費用率に依存。 |
| `vix` 米株オプション市場の予想変動率 | 株式デリバティブ・顧客ヘッジ需要とポジションリスク。 | 中程度の上昇はフロー増、急騰は損失/資産流出でマイナス。単調符号を仮定しない。 |
| `us_ig_credit_oas` 米投資適格社債信用スプレッド | 信用商品の評価・引受/資金調達・担保環境。 | 拡大は概して逆風だが顧客取引量増が相殺。 |
| `us_capital_markets_etf` 米資本市場業ETF | 海外証券/資産運用業の業績期待とセクター評価。 | 同方向仮説。個社の実収益ではなく市場価格の比較対象。 |

**現行設定で取得経路がある採用群:** `nikkei225_futures`, `sp500_futures`, `usdjpy`, `vix`。

**追加取得が必要:** `global_aggregate_bond_etf`, `japan_equity_turnover`, `us_ig_credit_oas`, `us_capital_markets_etf`。

INSUFFICIENT_DATA：追加取得/時点整合検証を終えるまで完全プロファイルとして予測しない。

## 8766 東京海上ホールディングス — 8群

国内外の損保引受リスクと日米の資産運用を銀行から分離する。自然災害は発行当時の予報/速報に限定し、事後損害額の混入を防ぐ。

公式事業根拠: [東京海上 FY2026 1Q説明資料（2026-08-12）](https://www.tokiomarinehd.com/en/ir/event/presentation/2026/f5hrqd000000a3c1-att/Overview_of_2026_1Q_Results_e.pdf), [東京海上 国内損保引受戦略（2026-09-04）](https://www.tokiomarinehd.com/en/ir/event/presentation/2026/f5hrqd000000boil-att/UW_Strategy_and_Capabilities_in_the_Japan_P%26C_e.pdf), [東京海上 Annual Investor Day London（2026-07-09）](https://www.tokiomarinehd.com/en/ir/event/presentation/2026/f5hrqd000000a39q-att/20260709_AID_presentation.pdf)。下表の採用理由・符号はその事業構造からの推論。

| ID / 指標 | 収益への経路 | 符号の条件 |
|---|---|---|
| `jgb_20y_yield` 日本20年国債利回り | 国内生命保険の長期資産負債・再投資と国内保険資産の金利環境。 | 利回り改善と債券/負債評価差の両面。銀行利鞘のような単純正符号ではない。 |
| `us_10y_yield` 米10年国債利回り | 北米の保険負債を裏付ける運用資産・再投資利回り。 | 金利低下は収入利回りに逆風、既存債評価には追い風。ALMで純効果は変化。 |
| `usdjpy` ドル円 | 海外保険利益の円換算と国内の外貨建て支払備金。 | 円安は海外換算益と国内外貨準備金増を同時に生む。 |
| `nikkei225_futures` 日本株市場（既存日経225先物代理） | 売却進行中の国内政策保有株式の価格/売却利益の代理。 | 株高は通常プラスだが保有削減に伴い感応度は低下。 |
| `us_ig_credit_oas` 米投資適格社債信用スプレッド | 北米債券/ローン資産の信用評価・期待信用損失。 | 拡大は通常逆風。投資適格OASは私募/CRE個別信用の不完全な代理。 |
| `us_insurance_equity_etf` 米保険業ETF | 海外引受収益・保険料サイクルに対する保険セクター評価。 | 通常同方向。KIEは生保等も含み、引受料率や再保険料の直接値ではない。 |
| `us_hurricane_risk` 北米ハリケーン予報リスク | 北米自然災害による保険金支払/再保険期待の変化。 | 脅威増は当面逆風だが再保険回収・翌期料率上昇で中期符号は変わる。 |
| `japan_natural_catastrophe_risk` 国内自然災害速報・警報リスク | 国内火災・風水害・地震関連損害の速報/事前警戒。 | 事故危険増は通常逆風。被災域・免責・保有/再保険が損益を左右。 |

**現行設定で取得経路がある採用群:** `us_10y_yield`, `usdjpy`, `nikkei225_futures`。

**追加取得が必要:** `jgb_20y_yield`, `us_ig_credit_oas`, `us_insurance_equity_etf`, `us_hurricane_risk`, `japan_natural_catastrophe_risk`。

INSUFFICIENT_DATA：追加取得/時点整合検証を終えるまで完全プロファイルとして予測しない。

## 各系列の取得元・頻度・08:30条件

「公式確認」は商品の実在や配信説明の確認で、歴史時点データの取得成功を意味しない。新規のYahoo等の識別子は推測でverified登録しない。

### `jgb_2y_yield` — 日本2年国債利回り

- 種別/状態: `proposed_direct` / `new_public_source_verified_ingestion_pending`
- 商品/系列: 財務省定残存2年国債金利（CSV列）
- 頻度: 日本営業日日次
- cutoff: 観測日15時価格→翌日本営業日09:30公表。08:30には前日観測は不可、通常t-2以前の最新公表済み値。休業日も公表カレンダーで判定。
- 確認内容/限界: 公式公表時刻確認済み。CSVパーサー、改訂履歴、受信時刻保存は未実装。2510.Tの債券価格を同一金利として代用しない。
- 根拠: [財務省 国債金利Q&A](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/qa.htm), [財務省 国債金利情報](https://www.mof.go.jp/jgbs/reference/interest_rate/index.htm)。公表日不明の説明ページは2026-10-15閲覧。

### `us_2y_yield` — 米2年国債利回り

- 種別/状態: `existing_direct` / `existing_config_verified_not_retested`
- 商品/系列: Official Daily Treasury Par Yield Curve 2Y
- 頻度: 日次、設定で許可する場合cutoff前スナップショット
- cutoff: configのsession_policy・market_timezone・freshnessをそのまま適用。published/first_observed/retrievedすべて08:30 JST以前。
- 確認内容/限界: ドル短期金利期待・再価格付けの代理。米10年/30年/スプレッドを追加して同じ群を水増ししない。 今回の接続/履歴再取得は未実施。
- 根拠: [US Treasury Daily Treasury Rates](https://home.treasury.gov/resource-center-data-chart-center/interest-rates)。公表日不明の説明ページは2026-10-15閲覧。

### `usdjpy` — ドル円

- 種別/状態: `existing_direct` / `existing_config_verified_not_retested`
- 商品/系列: JPY=X
- 頻度: 日次、設定で許可する場合cutoff前スナップショット
- cutoff: configのsession_policy・market_timezone・freshnessをそのまま適用。published/first_observed/retrievedすべて08:30 JST以前。
- 確認内容/限界: 海外損益換算と外貨負債の経路。 今回の接続/履歴再取得は未実施。
- 根拠: `config/indicators.yaml` のverifiedソース情報。詳細なprovider/market/endpointはJSONに保存。

### `nikkei225_futures` — 日本株市場（既存日経225先物代理）

- 種別/状態: `existing_direct` / `existing_config_verified_not_retested`
- 商品/系列: NIY=F
- 頻度: 日次、設定で許可する場合cutoff前スナップショット
- cutoff: configのsession_policy・market_timezone・freshnessをそのまま適用。published/first_observed/retrievedすべて08:30 JST以前。
- 確認内容/限界: TOPIXそのものではない。企業が保有する株式/AUMの国内市場価格代理。 今回の接続/履歴再取得は未実施。
- 根拠: `config/indicators.yaml` のverifiedソース情報。詳細なprovider/market/endpointはJSONに保存。

### `us_ig_credit_oas` — 米投資適格社債信用スプレッド

- 種別/状態: `proposed_direct` / `new_public_source_verified_ingestion_pending`
- 商品/系列: ICE BofA US Corporate OAS / FRED BAMLC0A0CM
- 頻度: 日次Close（公表ラグあり）
- cutoff: 公表/初回取得が08:30 JST以前の最新観測。10/13観測が10/14 10:26 CDT（10/15 00:26 JST）更新の実例。直前米国終値と決め打ちしない。
- 確認内容/限界: 系列定義・頻度・公表日時確認済み。ICE利用条件、2026年4月以降3年窓、過去vintage要確認。KRE/XLFは信用スプレッドではない。
- 根拠: [FRED ICE BofA US Corporate OAS](https://fred.stlouisfed.org/series/BAMLC0A0CM)。公表日不明の説明ページは2026-10-15閲覧。

### `morgan_stanley_equity` — Morgan Stanley株価（出資・協業先）

- 種別/状態: `proposed_proxy` / `new_instrument_verified_history_pending`
- 商品/系列: NYSE: MS
- 頻度: 米国営業日日次
- cutoff: 直近完了米国セッションの価格、公式取引所・タイムゾーン・企業行動・受信時刻を確認後に使用。08:30以降の訂正を遡及適用しない。
- 確認内容/限界: 実在銘柄/商品は一次資料で確認。Yahoo/EODHD等のプロバイダsymbolと全履歴可用性は未テスト。
- 根拠: [Morgan Stanley IR](https://www.morganstanley.com/about-us-ir)。公表日不明の説明ページは2026-10-15閲覧。

### `krungsri_equity` — タイKrungsri株価

- 種別/状態: `proposed_proxy` / `new_instrument_verified_history_pending`
- 商品/系列: SET: BAY（プロバイダsymbol未確定）
- 頻度: タイ営業日日次
- cutoff: 前日までの完了現地セッション。Asia/Bangkok、終値確定/受信を08:30 JSTより前に確認。
- 確認内容/限界: 子会社業績期待の代理。低流動性/同値連続/取引停止を欠損とゼロ変化で区別し、未取引価格を新情報にしない。
- 根拠: [Krungsri 企業概要](https://www.krungsri.com/en/about-krungsri)。公表日不明の説明ページは2026-10-15閲覧。

### `danamon_equity` — インドネシアDanamon株価

- 種別/状態: `proposed_proxy` / `new_instrument_verified_history_pending`
- 商品/系列: IDX: BDMN（プロバイダsymbol未確定）
- 頻度: インドネシア営業日日次
- cutoff: 前日までの完了現地セッション。Asia/Jakarta、終値確定/受信を08:30 JSTより前に確認。
- 確認内容/限界: 子会社業績期待の代理。浮動株/取引頻度・企業行動を検査し、薄商いによる同値継続を記録。
- 根拠: [Bank Danamon 企業概要](https://www.danamon.co.id/tentang-danamon/profile-perusahaan/tentang-bank-danamon)。公表日不明の説明ページは2026-10-15閲覧。

### `usd_jpy_cross_currency_basis` — 円ドル通貨ベーシス（外貨調達費用）

- 種別/状態: `proposed_direct` / `new_vendor_required_daily_history_unverified`
- 商品/系列: 同一期限・同一通貨脚/担保条件のUSDJPY通貨ベーシス（例3か月、ベンダー識別子は未確認）
- 頻度: OTC気配/日次fixが必要
- cutoff: 締切前の固定fixだけを使用。期限、basisの符号規約、bid/ask、中値、休日、時刻、初回受信を保存。
- 確認内容/限界: BISは経済機構の一次資料。BIS統計が日次ベーシス価格APIを提供すると主張しない。Bloomberg/LSEG等の提供可否・費用は要確認、未契約。
- 根拠: [BIS Covered interest parity lost](https://www.bis.org/publications/qr-201609/covered-interest-parity-lost-understanding-cross-currency-basis)。公表日不明の説明ページは2026-10-15閲覧。

### `jefferies_equity` — Jefferies株価（出資・協業先）

- 種別/状態: `proposed_proxy` / `new_instrument_verified_history_pending`
- 商品/系列: NYSE: JEF
- 頻度: 米国営業日日次
- cutoff: 直近完了米国セッションの価格、公式取引所・タイムゾーン・企業行動・受信時刻を確認後に使用。08:30以降の訂正を遡及適用しない。
- 確認内容/限界: 実在銘柄/商品は一次資料で確認。Yahoo/EODHD等のプロバイダsymbolと全履歴可用性は未テスト。
- 根拠: [Jefferies IR](https://ir.jefferies.com/overview/)。公表日不明の説明ページは2026-10-15閲覧。

### `india_bank_index` — インド銀行株指数

- 種別/状態: `proposed_proxy` / `new_instrument_verified_history_pending`
- 商品/系列: NSE Indices Nifty Bank（契約データ識別子未確定）
- 頻度: インド営業日日次
- cutoff: Asia/Kolkataの完了セッション終値、公開/取得が08:30 JST以前。利用権と履歴公表時刻を検証。
- 確認内容/限界: YES BANK/SMFG India Creditの業績そのものではない地域信用・銀行事業代理。中国FXIを代用しない。
- 根拠: [NSE Indices Nifty Bank](https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-bank)。公表日不明の説明ページは2026-10-15閲覧。

### `japan_card_transaction_value` — 国内カード決済取扱高

- 種別/状態: `proposed_direct` / `new_vendor_required_daily_history_unverified`
- 商品/系列: 加盟店/カード処理の日次集計（匿名化パネルまたはSMCC等の提供契約。公開の確定日次symbolなし）
- 頻度: 要件は日次。公表される月次/四半期取扱高は補助状態のみ
- cutoff: 日次集計の対象日・集計完了/発表時刻を別に記録。速報が08:30までに届かなければ前の公表済み日。
- 確認内容/限界: 無料公開の連続日次データは未確認。月次統計を日次に前方埋めして日次観測があると扱わない。取得契約ができなければ未取得のまま。
- 根拠: [SMBC Group IR Day 2026](https://www.smfg.co.jp/english/investor/financial/small/pdf/20260826irday_pre01e.pdf)。公表日不明の説明ページは2026-10-15閲覧。

### `us_10y_yield` — 米10年国債利回り

- 種別/状態: `existing_direct` / `existing_config_verified_not_retested`
- 商品/系列: Official Daily Treasury Par Yield Curve 10Y
- 頻度: 日次、設定で許可する場合cutoff前スナップショット
- cutoff: configのsession_policy・market_timezone・freshnessをそのまま適用。published/first_observed/retrievedすべて08:30 JST以前。
- 確認内容/限界: 米ドル長期資産運用・金利市場の基準。日本金利の代用品ではない。 今回の接続/履歴再取得は未実施。
- 根拠: [US Treasury Daily Treasury Rates](https://home.treasury.gov/resource-center-data-chart-center/interest-rates)。公表日不明の説明ページは2026-10-15閲覧。

### `us_capital_markets_etf` — 米資本市場業ETF

- 種別/状態: `proposed_proxy` / `new_instrument_verified_history_pending`
- 商品/系列: State Street KCE
- 頻度: 米国営業日日次
- cutoff: 直近完了米国セッションの価格、公式取引所・タイムゾーン・企業行動・受信時刻を確認後に使用。08:30以降の訂正を遡及適用しない。
- 確認内容/限界: 実在銘柄/商品は一次資料で確認。Yahoo/EODHD等のプロバイダsymbolと全履歴可用性は未テスト。
- 根拠: [State Street SPDR S&P Capital Markets ETF](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-capital-markets-etf-kce)。公表日不明の説明ページは2026-10-15閲覧。

### `japan_equity_turnover` — 東証株式市場の日次売買代金

- 種別/状態: `proposed_direct` / `new_public_source_verified_ingestion_pending`
- 商品/系列: JPX株式日報の市場別売買代金（対象市場/売買区分を固定）
- 頻度: 日本営業日日次
- cutoff: t-1引け後の発表済み日報を08:30前に取得。公表時刻が実証できない過去版は保守的なラグを設定。
- 確認内容/限界: 日報入口確認。日次値の抽出・初回配信時刻・履歴revision検証は未実施。株価リターンと異なる取引活動経路。
- 根拠: [JPX 株式統計・日報](https://www.jpx.co.jp/english/markets/statistics-equities/index.html)。公表日不明の説明ページは2026-10-15閲覧。

### `sp500_futures` — 米国株式市場（既存S&P500先物）

- 種別/状態: `existing_direct` / `existing_config_verified_not_retested`
- 商品/系列: ES=F
- 頻度: 日次、設定で許可する場合cutoff前スナップショット
- cutoff: configのsession_policy・market_timezone・freshnessをそのまま適用。published/first_observed/retrievedすべて08:30 JST以前。
- 確認内容/限界: 海外株式AUMとリスク選好の代理で、全世界株指数とは同一でない。 今回の接続/履歴再取得は未実施。
- 根拠: `config/indicators.yaml` のverifiedソース情報。詳細なprovider/market/endpointはJSONに保存。

### `global_aggregate_bond_etf` — 世界投資適格債券市場（運用AUM代理）

- 種別/状態: `proposed_proxy` / `new_instrument_verified_history_pending`
- 商品/系列: iShares Core Global Aggregate Bond UCITS ETF AGGG・USD分配型
- 頻度: 選定取引所の営業日日次
- cutoff: 取引所別商品/通貨/終値時刻を確定して完了セッションを使用。NAVは公表時刻が別なので混同しない。
- 確認内容/限界: 商品と運用対象確認済み、取引所別provider symbol/価格・分配履歴は未検証。米国債1本で世界AUMを置き換えない。
- 根拠: [iShares Core Global Aggregate Bond UCITS ETF](https://www.ishares.com/uk/individual/en/products/291773/ishares-global-aggregate-bond-ucits-etf-fund)。公表日不明の説明ページは2026-10-15閲覧。

### `vix` — 米株オプション市場の予想変動率

- 種別/状態: `existing_direct` / `existing_config_verified_not_retested`
- 商品/系列: ^VIX
- 頻度: 日次、設定で許可する場合cutoff前スナップショット
- cutoff: configのsession_policy・market_timezone・freshnessをそのまま適用。published/first_observed/retrievedすべて08:30 JST以前。
- 確認内容/限界: 水準/変化/移動平均を複数指標と数えない。 今回の接続/履歴再取得は未実施。
- 根拠: `config/indicators.yaml` のverifiedソース情報。詳細なprovider/market/endpointはJSONに保存。

### `jgb_20y_yield` — 日本20年国債利回り

- 種別/状態: `proposed_direct` / `new_public_source_verified_ingestion_pending`
- 商品/系列: 財務省定残存20年国債金利（CSV列）
- 頻度: 日本営業日日次
- cutoff: 観測日15時価格→翌日本営業日09:30公表。08:30には前日観測は不可、通常t-2以前の最新公表済み値。休業日も公表カレンダーで判定。
- 確認内容/限界: 公式公表時刻確認済み。CSVパーサー、改訂履歴、受信時刻保存は未実装。2510.Tの債券価格を同一金利として代用しない。
- 根拠: [財務省 国債金利Q&A](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/qa.htm), [財務省 国債金利情報](https://www.mof.go.jp/jgbs/reference/interest_rate/index.htm)。公表日不明の説明ページは2026-10-15閲覧。

### `us_insurance_equity_etf` — 米保険業ETF

- 種別/状態: `proposed_proxy` / `new_instrument_verified_history_pending`
- 商品/系列: State Street KIE
- 頻度: 米国営業日日次
- cutoff: 直近完了米国セッションの価格、公式取引所・タイムゾーン・企業行動・受信時刻を確認後に使用。08:30以降の訂正を遡及適用しない。
- 確認内容/限界: 実在銘柄/商品は一次資料で確認。Yahoo/EODHD等のプロバイダsymbolと全履歴可用性は未テスト。
- 根拠: [State Street SPDR S&P Insurance ETF](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-insurance-etf-kie)。公表日不明の説明ページは2026-10-15閲覧。

### `us_hurricane_risk` — 北米ハリケーン予報リスク

- 種別/状態: `proposed_event_proxy` / `new_constructed_event_history_pending`
- 商品/系列: NOAA NHC発行当時の予報・警報（事前定義した米沿岸リスクスコア）
- 頻度: イベント更新→各JPX営業日08:30の状態に集約
- cutoff: 08:30前の発行/取得電文のみ。事後確定Best Track/被害総額/Tropical Cyclone Reportを予報時点に戻さない。
- 確認内容/限界: 公的アーカイブの存在確認。120営業日以上の完全性、保険エクスポージャとのマッピング、無警報ゼロ/未取得NAを区別する仕様が必要。日次保険損失そのものではない。
- 根拠: [NOAA NHC Data Archive](https://www.nhc.noaa.gov/data/?exception=true&iap=false)。公表日不明の説明ページは2026-10-15閲覧。

### `japan_natural_catastrophe_risk` — 国内自然災害速報・警報リスク

- 種別/状態: `proposed_event_proxy` / `new_constructed_event_history_pending`
- 商品/系列: 気象庁防災XMLの台風/洪水等と発表済み地震情報を事前定義ルールで集約
- 頻度: イベント更新→各JPX営業日08:30の状態に集約
- cutoff: 各電文の発表/発見/取得が08:30以前。事後の確定震度・確定被害額は未来情報として排除。
- 確認内容/限界: 公開Atomは直近10分または数日で120日分はない。過去の発行時点アーカイブ取得か今後の蓄積が必須。自然災害なしと取得欠落は別。実保有保険契約の損害額推定ではない。
- 根拠: [気象庁 防災XML PULL](https://xml.kishou.go.jp/xmlpull.html)。公表日不明の説明ページは2026-10-15閲覧。

## 追加取得依頼（未実行）

### 優先1: `jgb_2y_yield`, `jgb_20y_yield`

財務省CSV取得、観測日/翌営業日09:30公表のPIT管理、改訂保存。毎朝は公表済み最新値のみ。

公的CSV入口確認。未接続。

前日金利が必要ならJSDA参照統計/許諾済み市場データの前日配信を別途検証。日本債券ETFはduration価格代理で金利そのものではない。

根拠: [財務省 国債金利Q&A](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/qa.htm), [財務省 国債金利情報](https://www.mof.go.jp/jgbs/reference/interest_rate/index.htm)。

### 優先1: `us_ig_credit_oas`

FRED/ICE定義・時刻付き取得、初回公表ラグ、3年窓制限/利用条件、vintageを確認。

公開系列確認、取得API/ファイルの本体検証は未実施。

XLF/KREはOAS代替なし。ICEまたは許諾済みベンダーの履歴が必要な場合もある。

根拠: [FRED ICE BofA US Corporate OAS](https://fred.stlouisfed.org/series/BAMLC0A0CM)。

### 優先1: `morgan_stanley_equity`, `jefferies_equity`, `krungsri_equity`, `danamon_equity`, `india_bank_index`, `us_capital_markets_etf`, `us_insurance_equity_etf`, `global_aggregate_bond_etf`

商品/上場は公式確認済み。実provider symbol、取引所/currency、分割/配当、休業日、前日価格の公表/受信時刻、少なくとも120営業日+warmupの取得試験。

新規。無料Yahoo等の可用性は未確認。許諾が必要な履歴は購入せず要件だけ提示。

新規が取れない場合は不足と明示、XLF/KRE/FXIに無言で置換しない。

根拠: [Morgan Stanley IR](https://www.morganstanley.com/about-us-ir), [Jefferies IR](https://ir.jefferies.com/overview/), [Krungsri 企業概要](https://www.krungsri.com/en/about-krungsri), [Bank Danamon 企業概要](https://www.danamon.co.id/tentang-danamon/profile-perusahaan/tentang-bank-danamon), [NSE Indices Nifty Bank](https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-bank), [State Street SPDR S&P Capital Markets ETF](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-capital-markets-etf-kce), [State Street SPDR S&P Insurance ETF](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-insurance-etf-kie), [iShares Core Global Aggregate Bond UCITS ETF](https://www.ishares.com/uk/individual/en/products/291773/ishares-global-aggregate-bond-ucits-etf-fund)。

### 優先1: `japan_equity_turnover`

JPX日報の日次売買代金を固定市場定義で取得し公開時刻を記録。

日報の存在確認。過去の初回発行版・時間の検証待ち。

月次平均売買代金は日次系列の代用品にしない。

根拠: [JPX 株式統計・日報](https://www.jpx.co.jp/english/markets/statistics-equities/index.html)。

### 優先2: `usd_jpy_cross_currency_basis`

同一期限のFXスワップ/ベーシス日次fixを許諾ベンダーから取得する設計・見積依頼の準備。

候補市場は確認、無料日次系列/契約/価格未確認。購入・問い合わせ未実施。

日米金利差・ドル円・ドル指数ではこの資金プレミアムを復元できない。

根拠: [BIS Covered interest parity lost](https://www.bis.org/publications/qr-201609/covered-interest-parity-lost-understanding-cross-currency-basis)。

### 優先2: `japan_card_transaction_value`

日次国内カード決済パネルの提供範囲/速報時刻/改訂/履歴/許諾を調査。SMCC直接KPIが非公開なら欠測のまま。

日次公開系列未確認。月次/四半期IRの情報は事業仮説の根拠に限定。

日次小売株指数は弱い価格代理という別仮説として提案可能だが、自動置換しない。

根拠: [SMBC Group IR Day 2026](https://www.smfg.co.jp/english/investor/financial/small/pdf/20260826irday_pre01e.pdf)。

### 優先2: `us_hurricane_risk`, `japan_natural_catastrophe_risk`

NHCの当時発行予報アーカイブとJMA電文を時刻付き保存。欠測/無警報を分離し事前固定の地域別スコアを日次化。

公的配信は確認。JMA長期feedは数日のみで120日履歴不足。必要に応じ配信アーカイブ提供者の許諾調査。

季節性/イベントが疎なため120行だけでは係数が安定しない場合がある。長期PIT履歴・別途イベント評価が必要。事後災害損失を遡及投入しない。

根拠: [NOAA NHC Data Archive](https://www.nhc.noaa.gov/data/?exception=true&iap=false), [気象庁 防災XML PULL](https://xml.kishou.go.jp/xmlpull.html)。

### 優先3: `jgb_curve_shape`, `topix_banks`, `topix_insurance`, `japan_corporate_credit_spread`, `insurance_rate_cycle`, `us_insurance_loss_cost_inflation`

追加研究用: JGBカーブ、JPX銀行/保険指数、国内信用スプレッド、再保険料率・損害コスト指標の定義と取得条件を確認。

JPX指数の存在は確認。国内社債スプレッドはJSDA/ベンダー等の債券条件調整が必要。保険料率・損害インフレは月次/四半期/更新期中心で、公表日から状態として使う。

日次を捏造しない。カーブ傾きは既存円金利群内の仕様変更候補として扱い、短期金利・長期金利・差分で3群に水増ししない。銀行/保険指数は自社包含と共通市場の重複を管理し、採用する場合は固定枠を事前に入替。

根拠: [財務省 国債金利Q&A](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/qa.htm), [JPX 指数一覧](https://www.jpx.co.jp/english/markets/indices/line-up/), [JPX 指数のリアルタイム値](https://www.jpx.co.jp/english/markets/indices/realvalues/index.html), [東京海上 国内損保引受戦略](https://www.tokiomarinehd.com/en/ir/event/presentation/2026/f5hrqd000000boil-att/UW_Strategy_and_Capabilities_in_the_Japan_P%26C_e.pdf)。

## 過去の国内金利・銀行ETF研究の扱い

63セッション・1386予測、22銘柄へ既存productionにproxyセットを追加した比較。

production_ratesは集計Rank ICが0.1119→0.0917、金融5銘柄平均MAE差+0.0069pp（5社中1社のみ改善）と記載。

当該セット/期間の悪化は尊重する。金利の一次観測・企業ごとの固定7〜10群・独立した追加期間の比較ではない。特定企業の事業仮説を否定する十分な証拠でも、今回の手法が改善する証拠でもない。2510.T/1615.T等の当時の取得成功を2026-10-15時点の接続確認に転用しない。

出典: [2026-08-17既存調査](stock_indicator_research_2026-08-17.md)。数値は同文書の記載を引用しており、今回の再検証結果ではない。

## 実装へ渡す条件

固定群を全て明示し、データが揃う会社だけ予測する。原系列に複数の窓を作っても指標数は増えない。正則化の強さは調整できるが、結果がよく見えるまで事後に群を入替えない。符号の事前仮説は説明用で、単純な強制符号制約にしない。派生列の欠損処理/標準化は学習fold内でfitし、評価日は完全に外に置く。

発行時刻・初回取得時刻・観測日・改訂版を別に保管する。新規指標のPIT履歴と独立OOSを整備し、各社で共通市場だけのbaselineとの差、MAE/方向/Brier/区間の実測被覆を比較する。事業の説明力と寄り→引け予測の有効性は別に記録する。

## 資料の日付と閲覧範囲

- [MUFG Investors Day 2026](https://www.mufg.jp/dam/ir/presentation/2026/pdf/slides260901_en.pdf) — 資料日: 2026-09-09、閲覧: 2026-10-15。本文pp.10–16, 34–36, 39–49。公開日はIR一覧の9月9日、ファイル名の日付を公開日とみなさない。
- [MUFG FY2026 IR資料一覧](https://www.mufg.jp/english/ir/presentation/2026/index.html) — 資料日: 2026-10-02、閲覧: 2026-10-15。2026年9月公開資料と10月2日のスクリプト・Q&A更新を確認。
- [SMBC Group IR Day 2026](https://www.smfg.co.jp/english/investor/financial/small/pdf/20260826irday_pre01e.pdf) — 資料日: 2026-08-26、閲覧: 2026-10-15。pp.4–10 国内預貸・決済、23・28–33 Jefferies/アジア、34以降運用・トランザクション。
- [SMFG Investor Meeting FY3/2026](https://www.smfg.co.jp/english/investor/financial/latest_statement/2026_3/2026_fy_e_pre.pdf) — 資料日: 2026-05-18、閲覧: 2026-10-15。業績・事業構成の参照資料。
- [MIZUHO IR Day 2026](https://www.mizuho-fg.co.jp/investors/financial/briefing/pdf/20260907_1.pdf) — 資料日: 2026-09-11、閲覧: 2026-10-15。pp.28–30 グローバルCIBと米州FICC/欧米連携。公開日は一覧の9月11日。
- [みずほ2025年度決算会社説明会](https://www.mizuho-fg.co.jp/investors/financial/briefing/pdf/20260519_1.pdf) — 資料日: 2026-05-19、閲覧: 2026-10-15。2026-07-06に39ページ更新。金利収支、国内企業・WM、米州CIB、Mizuho-Greenhillを確認。
- [Nomura Investor Day 2026 CEO資料](https://www.nomuraholdings.com/en/investor/presentation/main/017/teaserItems1/00/tableContents/00/multiFileUpload30/link/2026_0529_01_prem.pdf) — 資料日: 2026-05-29、閲覧: 2026-10-15。公式検索索引で資料の所在とWM/IM/Wholesale/Banking構成を確認。直接PDF取得は403。
- [野村 FY2026/27 1Q公式決算解説](https://plus.nomuraholdings.com/en/latest/20260729-FY202703-1Q-Highlights.html) — 資料日: 2026-07-29、閲覧: 2026-10-15。公式本文を閲覧。WM継続収益、国内外AUM/運用報酬、Wholesale顧客フロー、銀行の担保融資を確認。
- [Macquarie米欧公開資産運用事業の買収発表](https://www.nomuraholdings.com/en/news/nr/news20250422.html) — 資料日: 2025-04-22、閲覧: 2026-10-15。株式・債券・マルチアセットの事業対象を確認。2026年の買収後業績はQ1公式解説で補強。
- [東京海上 FY2026 1Q説明資料](https://www.tokiomarinehd.com/en/ir/event/presentation/2026/f5hrqd000000a3c1-att/Overview_of_2026_1Q_Results_e.pdf) — 資料日: 2026-08-12、閲覧: 2026-10-15。pp.8–9海外運用、14–19国内資産負債、22自然災害、23–25信用/不動産/為替を確認。
- [東京海上 国内損保引受戦略](https://www.tokiomarinehd.com/en/ir/event/presentation/2026/f5hrqd000000boil-att/UW_Strategy_and_Capabilities_in_the_Japan_P%26C_e.pdf) — 資料日: 2026-09-04、閲覧: 2026-10-15。pp.2–7自然災害/損害インフレ/火災、9–11米賠償責任、16再保険。
- [東京海上 Annual Investor Day London](https://www.tokiomarinehd.com/en/ir/event/presentation/2026/f5hrqd000000a39q-att/20260709_AID_presentation.pdf) — 資料日: 2026-07-09、閲覧: 2026-10-15。pp.11–12 Delphi/運用、23–24金利・信用・自然災害と再保険。
- [財務省 国債金利Q&A](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/qa.htm) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。15時価格に基づく固定利付JGBの複利・定残存金利。翌営業日09:30公表。
- [財務省 国債金利情報](https://www.mof.go.jp/jgbs/reference/interest_rate/index.htm) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。CSV入口。新規時系列の本体取得・履歴のPIT検査は未実施。
- [FRED ICE BofA US Corporate OAS](https://fred.stlouisfed.org/series/BAMLC0A0CM) — 資料日: 2026-10-14、閲覧: 2026-10-15。日次Close。10/13観測が10/14 10:26 CDT更新。2026年4月以降3年分のみ提供という注記。
- [JPX 指数一覧](https://www.jpx.co.jp/english/markets/indices/line-up/) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。TOPIXと業種別指数の入口。履歴取得/利用条件の検証は別途必要。
- [JPX 指数のリアルタイム値](https://www.jpx.co.jp/english/markets/indices/realvalues/index.html) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。分次表示と日次指数の区別あり。表示ページが自動取得/履歴利用権を保証しない。
- [JPX 株式統計・日報](https://www.jpx.co.jp/english/markets/statistics-equities/index.html) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。日報あり。日次売買代金の抽出・初回公表時刻の確認が必要。月次の1日平均系列と混同しない。
- [Morgan Stanley IR](https://www.morganstanley.com/about-us-ir) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。NYSE:MSを公式確認。市場データ取得は未試行。
- [Jefferies IR](https://ir.jefferies.com/overview/) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。NYSE:JEFを公式確認。市場データ取得は未試行。
- [Krungsri 企業概要](https://www.krungsri.com/en/about-krungsri) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。SET:BAYを公式確認。Yahoo等のプロバイダ識別子は未検証。
- [Bank Danamon 企業概要](https://www.danamon.co.id/tentang-danamon/profile-perusahaan/tentang-bank-danamon) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。BEI/IDX:BDMNを公式確認。プロバイダ識別子は未検証。
- [NSE Indices Nifty Bank](https://www.niftyindices.com/indices/equity/sectoral-indices/nifty-bank) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。インド銀行株ベンチマークを確認。YES BANKそのものの価格ではない。
- [State Street SPDR S&P Capital Markets ETF](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-capital-markets-etf-kce) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。KCE。IB/Brokerageだけでなく運用、取引所、カストディ等を含む。
- [State Street SPDR S&P Insurance ETF](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-insurance-etf-kie) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。KIE。米保険セクターの代理で純粋な損保だけの指数ではない。
- [iShares Core Global Aggregate Bond UCITS ETF](https://www.ishares.com/uk/individual/en/products/291773/ishares-global-aggregate-bond-ucits-etf-fund) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。AGGG・USD分配型のグローバル投資適格債券ETFを確認。取引所別識別子、価格/配当履歴、利用権は未検証。
- [BIS Covered interest parity lost](https://www.bis.org/publications/qr-201609/covered-interest-parity-lost-understanding-cross-currency-basis) — 資料日: 2016-09、閲覧: 2026-10-15。円ドルベーシスは円金利/米金利の差ではなくFXスワップ資金調達プレミアム。公開日次フィードを確認した根拠ではない。
- [NOAA NHC Data Archive](https://www.nhc.noaa.gov/data/?exception=true&iap=false) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。発行当時の予報電文アーカイブと事後Best Track/Tropical Cyclone Reportsを区別。
- [気象庁 防災XML PULL](https://xml.kishou.go.jp/xmlpull.html) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。高頻度Atomは毎分/少なくとも直近10分、長期Atomは毎時/数日。120営業日アーカイブを自動保証しない。
- [US Treasury Daily Treasury Rates](https://home.treasury.gov/resource-center-data-chart-center/interest-rates) — 資料日: ページに固定公表日なし、閲覧: 2026-10-15。既存公式XMLルートはconfigでverified。今回の新規取得テストはしない。

公式IR/公的データ説明ページの読取、ローカル既存設定/研究の読取、文書2点の作成のみ。購入・口座・外部送信・DB更新なし。

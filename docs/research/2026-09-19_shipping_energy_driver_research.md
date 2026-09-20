# Shipping / energy driver research — 2026-09-19

Research-only. No factor is promoted by this document. The target is **Close / Open − 1**, not the overnight gap. Business exposure motivates a hypothesis; it does not establish intraday predictive power. Current configurations below are from fetched `origin/main`, not the approximately 90-commit-old local checkout. References were accessed on 2026-09-19.

## Production observed in code

`services/dataset.py::_indicator_ids` expands `common + sector + applies_to_tickers`, then filters `resolution_status == resolved` and `enabled`. The ten forced common series are `vix, usdjpy, eurjpy, dollar_index, nikkei225_futures, sp500_futures, nasdaq100_futures, us_2y_yield, us_10y_yield, us_30y_yield` (denoted **C10** below). The shipping addition **S6** is `baltic_dry_index, fxi, copper, wti, brent, audjpy`. The energy addition **E6** is `wti, brent, xle, oih, natural_gas, fxi`.

Important: `baltic_dry_index` currently fetches **BDRY, a US ETF proxy**, not the cash Baltic Dry Index. BCI, BPI, iron ore and an unspecified US shipping-equity proxy are pending and therefore not active. The earlier CSV with 27 indicators is not the current production truth. The September 8 reduction record removed redundant factors and disabled ADRs; it explicitly did **not** claim a new OOS improvement. The historical individual-versus-pooled study found MAE 1.2283 versus 1.2692 percentage points, direction 0.5599 versus 0.5123, so naive sector pooling stays a negative control.

## Per-ticker hypotheses and evidence

| Ticker | Current inputs | Prioritized research candidates, not selected | Deferred / excluded from promotion | Rationale and expected relationship |
|---|---|---|---|---|
| 9101 日本郵船 | C10 + S6 | One dry-bulk factor (BDRY or licensed BDI), container-rate proxy, USDJPY; separately LNG/tanker peer return and FXI | BDI+BCI+BPI together; simultaneous WTI+Brent+all energy peers; unverified OTC ADR; LNG spot price as direct shipping earnings | Diversified liner/automotive/dry bulk/energy. Freight and weaker JPY may support earnings, fuel inflation may hurt; overnight-to-intraday sign is unproven. |
| 9104 商船三井 | C10 + S6 | LNG/ethane shipping peer, one dry-bulk factor, USDJPY, container proxy; separately tanker and chemical shipping proxy | Henry Hub as a mechanical proxy for contracted LNG transport; indiscriminate commodity basket; unverified OTC ADR | LNG capacity/long contracts, chemical shipping, real property distinguish MOL. Freight, fuel, contract mix and FX can offset each other. |
| 9107 川崎汽船 | C10 + S6 | Car-carrier peer return, one dry-bulk factor, container proxy, USDJPY; Toyota/Honda overnight returns as challengers only | Production use of stale ADRs; BCI/BPI without licensed history; all global auto stocks | Car-carrier utilization and bunker costs matter alongside ONE; auto demand information may diffuse during Tokyo but usually already affects the auction. |
| 1605 INPEX | C10 + E6 | Brent, USDJPY, separately LNG/JKM or suitable gas producer return; one broad energy control; Brent–WTI only if incremental | Henry Hub interpreted as Japanese LNG contract price; OIH as direct production proxy; forced FXI/three yield tenors | Upstream oil and gas; positive fundamental Brent/JPY-depreciation exposure, but interruption risk and opening-price absorption can reverse intraday sign. |
| 5020 ENEOS | C10 + E6 | USDJPY, crude benchmark, gasoline/distillate crack spread, refining-equity peer; Singapore/Dubai margins if licensed | Automatic copper addition based on pre-reorganization structure; OIH without mechanism; unverified OTC ADR | Refining margins and inventory timing differ from upstream oil-price exposure. US cracks are regional proxies, not Japanese realized margins. |
| 5019 出光興産 | C10 + E6 | Product cracks, USDJPY, crude; coal only after current resources verification; LNG as separate small challenger | Coal basket by industry label; growth-plan LNG exposure treated as current spot sensitivity; battery news NLP | Petroleum dominates recent segment earnings but resources and materials remain. Product margin effect differs from crude inventory gains. |
| 5021 コスモ | C10 + E6 | Product cracks, USDJPY, crude/Dubai proxy; separate upstream control | INPEX-style oil-only set; pure-refiner assumption; automatic Henry Hub/OIH | Large petroleum and Middle-East E&P contributions; physical production disruption can offset higher crude prices. |

**Selected:** existing champion inputs remain pending nested OOS, valid historical availability and forward evidence. “Deferred” is an eligibility/rationale decision, not a measured rejection. No new candidate has an OOS result in this subtask.

### 9101 日本郵船

The August 5 FY2026 Q1 briefing reports recurring profit of ¥71.2bn, including automotive ¥16.8bn, dry bulk ¥19.4bn and energy ¥23.9bn. Higher operating costs hurt automotive; LNG carriers have medium/long contracts. Remaining-nine-month sensitivity is approximately +¥1.84bn per ¥1/USD depreciation and +¥0.58bn per $10/ton bunker decrease. Segment definitions changed in Q1, including steaming coal moving to dry bulk. Do not silently stitch segment histories. [NYK Q1 presentation, pp.5–13](https://www.nyk.com/english/ir/library/result/2026/__icsFiles/afieldfile/2026/08/05/260805_ppt_en.pdf)

Recent official news includes July 30 MidOcean Energy investment, July 31 planned NS United tender offer, and September 1 Avenir LNG operating structure. These justify separately researching gas transport and dry bulk, but are corporate events, not continuous daily factors. [NYK news archive](https://www.nyk.com/english/news/)

Geographic interpretation: global sea lanes, China-linked bulk/container demand and Europe/North-America logistics; the latest presentation does not provide a complete comparable regional profit matrix. **Regional profit percentages and a current numeric rate sensitivity remain unverified.** Balance-sheet funding risk is not a reason to force all US yields into the stock model.

### 9104 商船三井

The August 7 Q1 presentation separates chemical logistics from energy/product transport. FY2026 profit-before-tax forecasts include energy ¥74bn, chemical logistics ¥22bn, product transport ¥120bn (container ¥59bn) and dry bulk ¥27bn; these are forecasts, not actual Q1 shares. Its fleet table shows 111 LNG carriers at June 30 and only one market-exposed vessel within the LNG/ethane/gas-infrastructure subtotal. The published FX/bunker sensitivity is still explicitly dated April 30. [MOL Q1 presentation, pp.6–10,20](https://ir.mol.co.jp/en/ir/main/00/teaserItems1/00/linkList/00/link/%28E%292026.1Q%20Business%20Performance.pdf)

Recent official business developments include April 17 INPEX long-term LNG charter and June 4 US offshore LNG liquefaction investment. The latest Q&A discusses Hormuz disruptions and timing assumptions. [MOL Q1 Q&A](https://ir.mol.co.jp/en/ir/main/00/teaserItems1/00/linkList/02/link/%28E%292026_1Q_QA.pdf), [official news](https://www.mol.co.jp/en/pr/)

US LNG infrastructure, global transport and domestic/overseas real estate motivate different channels. **No complete latest regional profit percentages or quantified interest-rate sensitivity were verified.** Contracted transport exposure is materially different from being long spot natural gas.

### 9107 川崎汽船

The August 4 Q1 presentation reports ordinary profit ¥24.0bn and a FY forecast of ¥135bn. Nine-month sensitivity is ±¥1.3bn per ¥1/USD and ±¥0.64bn per $10/ton bunker. Forecasts explicitly assume Hormuz passage restarting in October and continued Cape routing instead of Suez. [K Line Q1 presentation, pp.5–8](https://www.kline.co.jp/en/ir/library/presentation/main/0111111111117/teaserItems1/0/linkList/01/link/2026_1_presentation_e.pdf)

Management explains car-carrier pressure through Gulf access, trapped ships, reduced turnover and higher operating/charter costs; dry-bulk demand was comparatively firm. Container rates and fuel costs do not have the same profit sign. This supports independent car-carrier, dry-bulk and fuel channels, not an undifferentiated shipping factor. [Q1 Q&A](https://www.kline.co.jp/en/ir/library/presentation/qa202601.html), [Q1 explanation](https://www.kline.co.jp/en/ir/library/presentation/ov202601.html)

China iron-ore/steel and grain trade, Middle-East vehicle routes and ONE global container lanes are relevant regions. **Latest regional profit weights, numerical interest-rate sensitivity and reliable overseas K Line ADR history remain unverified.** US-listed auto returns and Oslo car-carrier equities must be tested separately; Tokyo Toyota/Honda same-day prices are unavailable at 08:30.

### 1605 INPEX

August 7 H1 results show oil revenue ¥694.9bn and gas ¥271.9bn. Gas cannot be dropped from the economic rationale. [INPEX financial summary](https://www.inpex.com/english/ir/financial/summary.html)

The appendix reports H1 segment profit: Japan ¥7.9bn, Ichthys ¥173.1bn and other overseas ¥77.9bn. Net production is 98% overseas, with gas 267 of 629 thousand BOE/day. Its sensitivity table is dated February 12: Brent +$1/bbl contributes +¥5.5bn from Q1 start but +¥1.9bn from Q3 start; ¥1/USD depreciation contributes +¥3bn. Some gas sales use lagged oil pricing. These accounting sensitivities are neither daily regression coefficients nor intraday expected signs. [INPEX H1 appendix, pp.3–7](https://www.inpex.com/english/ir/library/upload/result20260807_e.pdf)

Recent result commentary cites lower Abu Dhabi oil volumes, stable Ichthys and Middle-East uncertainty. September 16 Abadi engineering progress confirms an Indonesia LNG development channel, without establishing a daily price signal. [H1 presentation](https://www.inpex.com/english/ir/library/upload/result20260807_b.pdf), [Worley primary announcement](https://www.worley.com/en/insights/our-news/conventional-energy/2026/delivers-feeds-indonesia-flagship-lng-and-ccs-development)

Interest rates may change financing/valuation but no current numerical rate sensitivity was established. Henry Hub, JKM and oil-linked LNG prices must never be treated as interchangeable.

### 5020 ENEOS

The official announcement confirms August 7 FY2026 Q1 results. The dynamic IR library did not expose its current numerical tables in the read-only web client; **latest segment/regional profit and numerical sensitivity extraction are incomplete**. Do not substitute older numbers. Current public business categories include petroleum products, oil/gas E&P, high-performance materials, electricity and renewables. [Q1 announcement](https://www.hd.eneos.co.jp/english/news/release_information/year/2026/financial_results_for_fiscal_2026_1q/), [IR library](https://www.hd.eneos.co.jp/english/ir/library/statement/)

The public risk page describes domestic product/crude margins, Asian petrochemical demand, currency exposure, inventory timing and interest-bearing debt. It labels its judgments **June 26, 2024**, so the old copper/metals discussion is not proof of current consolidated exposure. [ENEOS risk disclosure](https://www.hd.eneos.co.jp/english/about/risk/)

May shareholder materials describe Southeast Asia/Australia refining/marketing acquisition plans; the August 7 TPC announcement adds a materials-business hypothesis. These changes require a current ownership/segment check before using copper or US chemical peers. [ENEOS current official news](https://www.hd.eneos.co.jp/english/)

Prioritize refinery margin challengers against outright crude, with XLE/OIH retained only as controls. Do not claim this research completed ENEOS's latest quantitative exposure audit.

### 5019 出光興産

Official FY2025 segment data show operating-plus-equity income excluding inventory effects: petroleum ¥207.1bn, resources ¥33.1bn, functional materials ¥33.4bn, basic chemicals −¥6.8bn and power/renewables −¥1.8bn (before other/reconciliation). Petroleum dominates, but resources cannot be assumed zero. [Official segment table](https://www.idemitsu.com/en/ir/finance/segmentinformation/index.html)

The August 7 Q1 presentation confirms Pacific fuel trading, IMEA expansion, MidOcean investment/LNG development and current refinery utilization priorities. Its growth-investment allocation is **not** regional revenue or profit composition. Current numerical FX/coal/product-margin sensitivity tables did not extract reliably and remain unverified; do not recycle FY2024 sensitivity values. [Q1 presentation](https://www.idemitsu.com/jp/news/2026/260807_1.pdf), [Q1 financial results](https://www.idemitsu.com/jp/news/2026/260807_2.pdf)

The August results are a recent event hypothesis for product margins, inventory and trading conditions. Future battery/LNG growth announcements do not justify immediate sentiment or commodity factors. Coal must use licensed historical Newcastle/appropriate benchmark availability; a coal-equity proxy is a distinct instrument with its own operating risk.

### 5021 コスモエネルギー

August 6 Q1 materials report FY2025 ex-inventory ordinary profits of petroleum ¥92.8bn and E&P ¥65.3bn, with petrochemicals −¥3.1bn. FY2026 forecasts are ¥56bn and ¥38bn respectively; do not mix forecasts with actuals. E&P is centered on UAE/Qatar operations. The sensitivity table separates inventory from fuel cost; it conditions E&P sensitivity on normal production and notes lagged Dubai reference pricing for Murban. [Cosmo Q1 presentation, pp.23–25,28,33](https://www.cosmo-energy.co.jp/content/dam/corp/jp/ja/ir/financial/presentation/2026/q1/pdf/presen2026_1q.pdf)

The same current disclosure discusses supply diversification, alternative crude and production limits from Hormuz disruption. Hence rising oil can improve price realizations while falling physical volume hurts. September SAF and August CCS developments are monitored corporate events, not daily news features. [Cosmo official news/IR](https://www.cosmo-energy.co.jp/en/top.html)

A US crack spread may proxy refining conditions but not Japan's selling prices, subsidies, product purchases or the actual feedstock mix. Regional sales percentages and numeric rate sensitivity remain unverified. OIH is not automatically appropriate for a Japanese refiner with upstream assets.

## Indicator feasibility and 08:30 rules

| Candidate / instrument | Provider / market / timezone | Earliest permitted use | Feasibility / history / fallback decision |
|---|---|---|---|
| BDI/BCI/BPI | Baltic licensed feed, London | Published prior-London-session observation with actual availability ≤ cutoff | Direct series absent/pending in current repo. Historical licensed release times/rights unverified. BDI includes Capesize, Panamax **and Supramax**; it is not exactly spanned by BCI+BPI. |
| BDRY | Existing Yahoo / US cash / America/New_York | Completed cash session, normally 04:00 JST summer / 05:00 winter, plus provider delay | Existing proxy, not BDI level. EODHD configured fallback but entitlement/freshness must be measured; rolling futures introduce basis/roll effects. |
| Container rates (SCFI/CCFI) | Shanghai Shipping Exchange / Asia/Shanghai; licensed archive | Each weekly release only after its actual release time | Do not interpolate weekly observations into invented daily information. History/redistribution not verified; NYK charts are monthly summaries, not historical release archives. |
| Container/bulk/LNG/car-carrier equity peers | ZIM or separately chosen bulk/LNG peer; Oslo car-carrier listings | Prior completed issuer-exchange session and received-before-cutoff record | Symbols, survivorship, corporate actions, volume and coverage must be fetched before admission. No ticker guessed into production. Peers are not equivalent to freight indices. |
| WTI CL, Brent BZ, natural gas NG, copper HG | Existing Yahoo futures; CME/NYMEX underlying | Final prior-session bar, or timestamped immutable snapshot ≤08:30 | Config “verified” does not prove historical snapshot availability. Rolling continuous bars and current unfinished daily bars are unsafe. Current missing/stale rates not measured here. |
| RBOB RB / ULSD HO | CME/NYMEX; Yahoo `RB=F` / `HO=F` research routes | Synchronized completed observations, each leg available ≤08:30 | Symbols/history/publish latency not yet live-validated. FREE_UNVERIFIED until measured; no silent forward fill of missing leg. |
| Gasoline / distillate crack | Derived RB–CL / HO–CL | Availability = max(all input availability); matching trading date and delivery month | If product quote is USD/gallon: `42 * product - crude` USD/bbl. Verify source units; cents require /100 first. Same-contract expiry and roll policy required. |
| Brent–WTI | Derived matched-contract Brent/WTI | max of both legs' availability | ICE/CME dates, settlement clocks and expiry differ. A difference of arbitrary continuous front contracts is only an indicative proxy. |
| JKM LNG / TTF / Newcastle coal / Singapore product margins / Dubai/Murban | Licensed exchange/price agency feeds | Explicit release timestamps and historical vintages | Relevant economic candidates; no demonstrated free stable archive or rights in this task. PAID/UNVERIFIED_RESEARCH, not production. |
| USDJPY/AUDJPY/DXY | Existing Yahoo FX/futures | Actual captured quote timestamp ≤08:30 and configured age cap | Historical daily bars cannot stand in for historical 08:30 snapshots. EOD fallback is a separately defined feature, not a same-day live quote. |
| FXI / MCHI / XLE / OIH / SPY / QQQ | US cash ETFs, America/New_York | Prior completed session plus publication delay | Use exchange calendar/DST/early-close checks. FXI/MCHI and SPY/futures duplicates need training-only clustering; no automatic universal inclusion. |
| US2Y/10Y/30Y | Treasury official feed | Published observation actually available before cutoff | Official daily rate is indicative, not a tradable overnight quote. Do not fit 2Y,10Y and their exact difference simultaneously. |
| OTC ADRs of these seven companies | Issuer/depositary + verified market data required | Last actual trade/publication before cutoff | No verified high-quality liquid ADR route established. Exclude from promotion pending volume, stale-day and corporate-action checks. |

Official definitions: [Baltic index methodology](https://www.balticexchange.com/en/data-services/market-information0/indices.html), [Baltic benchmark guide and local-London publication times](https://www.balticexchange.com/content/dam/balticexchange/consumer/data-services-/documents-/ocean-bulk/GMB.pdf), [CME crack-spread mechanics](https://www.cmegroup.com/articles/2024/trading-crack-spreads.html). These establish economic definitions; they do **not** verify a free feed's historical arrival times.

## News research limitations and production decision

The review window is March 23–September 19, 2026 (180 days), with June 21 onward the 90-day subset. Official May/August results and April–September corporate releases above were reviewed. Targeted Reuters-domain searches for the latest seven-company results returned no accessible results in this session. No claim is made that a comprehensive Reuters/Bloomberg/FT/WSJ market-news archive was read. Current official shipping/energy disclosures support the disruption/cost/contract hypotheses; no news text was ingested as a feature.

Any subsequent news arm must persist `published_timestamp`, `available_timestamp`, `retrieved_at`, source, URL, content hash, license/terms and timezone. Historical ingestion availability is currently **not verified**. Company releases after the Tokyo close can inform the following valid session; they cannot be backdated into that morning's feature row. Market move attribution is not treated as a causal or OOS result.

## Required experiment before any promotion

Compare each proposed small set against the exact champion on identical eligible dates. Inner training-only selection must enforce coverage, staleness, availability, correlation clustering and coefficient-sign/rank stability, with no more than floor(n_train/10) final features and preferably 3–8. Outer chronological folds assess return error, rank/direction, probability calibration, interval coverage and 0/5/10/15/20bp costs. Paired bootstrap must resample dates, retaining same-date cross-stock dependence. Regime descriptions above are not labels chosen using future outcomes. Freeze feature/model/calibration/threshold versions, then register at least 20 new trading sessions of forward evaluation. This document neither tunes thresholds nor reuses a previously viewed holdout as sealed evidence.

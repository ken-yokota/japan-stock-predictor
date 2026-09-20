# Indicator feasibility / 2026-09-19

保存済みrawデータの監査。providerへの新規接続probeではない。空欄を0%と解釈しない。公表遅延・休場を含む期待sessionの再構成が未完了のため、欠測率・stale率の包括的PASSは未判定。

|Indicator|Symbol|Provider|Timezone / close|08:30設定|保存行数|保存範囲|品質|
|---|---|---|---|---|---:|---|---|
|sp500|^GSPC|yahoo_finance|America/New_York / 16:00|available|585|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 585}|
|nasdaq100|^NDX|yahoo_finance|America/New_York / 16:00|available|585|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 585}|
|dow|^DJI|yahoo_finance|America/New_York / 16:00|available|585|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 585}|
|vix|^VIX|yahoo_finance|America/New_York / 16:00|available|575|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 575}|
|usdjpy|JPY=X|yahoo_finance|UTC / None|conditional|539|2024-08-01〜2026-09-17|{'FREE_UNVERIFIED': 539}|
|eurjpy|EURJPY=X|yahoo_finance|UTC / None|conditional|539|2024-08-01〜2026-09-17|{'FREE_UNVERIFIED': 539}|
|dollar_index|DX-Y.NYB|yahoo_finance|UTC / None|conditional|544|2024-08-01〜2026-09-17|{'FREE_UNVERIFIED': 544}|
|nikkei225_futures|NIY=F|yahoo_finance|UTC / None|conditional|546|2024-08-01〜2026-09-17|{'FREE_UNVERIFIED': 546}|
|sp500_futures|ES=F|yahoo_finance|UTC / None|conditional|547|2024-08-01〜2026-09-17|{'FREE_UNVERIFIED': 547}|
|nasdaq100_futures|NQ=F|yahoo_finance|UTC / None|conditional|547|2024-08-01〜2026-09-17|{'FREE_UNVERIFIED': 547}|
|gold|GC=F|yahoo_finance|UTC / None|conditional|570|2024-08-01〜2026-09-17|{'FREE_UNVERIFIED': 547, 'DELAYED': 23}|
|us_2y_yield|None|us_treasury|America/New_York / None|conditional|573|2024-06-03〜2026-09-17|{'OFFICIAL': 573}|
|us_10y_yield|None|us_treasury|America/New_York / None|conditional|573|2024-06-03〜2026-09-17|{'OFFICIAL': 573}|
|us_30y_yield|None|us_treasury|America/New_York / None|conditional|573|2024-06-03〜2026-09-17|{'OFFICIAL': 573}|
|us_10y_minus_2y_spread|None|internal|None / None|conditional|573|2024-06-03〜2026-09-17|{'OFFICIAL': 573}|
|baltic_dry_index|BDRY|yahoo_finance|America/New_York / 16:00|available|584|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 584}|
|baltic_capesize_index|None|eodhd_free|None / None|pending|0|None〜None|{}|
|baltic_panamax_index|None|eodhd_free|None / None|pending|0|None〜None|{}|
|fxi|FXI|yahoo_finance|America/New_York / 16:00|available|724|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 724}|
|mchi|MCHI|yahoo_finance|America/New_York / 16:00|available|725|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 725}|
|copper|HG=F|yahoo_finance|UTC / None|conditional|568|2024-08-01〜2026-09-17|{'FREE_UNVERIFIED': 547, 'DELAYED': 21}|
|iron_ore|None|eodhd_free|None / None|pending|0|None〜None|{}|
|wti|CL=F|yahoo_finance|UTC / None|conditional|570|2024-08-01〜2026-09-17|{'FREE_UNVERIFIED': 547, 'DELAYED': 23}|
|brent|BZ=F|yahoo_finance|UTC / None|conditional|563|2024-08-01〜2026-09-17|{'FREE_UNVERIFIED': 547, 'DELAYED': 16}|
|audjpy|AUDJPY=X|yahoo_finance|UTC / None|conditional|539|2024-08-01〜2026-09-17|{'FREE_UNVERIFIED': 539}|
|us_shipping_equity_proxy|None|eodhd_free|None / None|pending|0|None〜None|{}|
|xle|XLE|yahoo_finance|America/New_York / 16:00|available|704|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 704}|
|oih|OIH|yahoo_finance|America/New_York / 16:00|available|616|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 616}|
|natural_gas|NG=F|yahoo_finance|UTC / None|conditional|570|2024-08-01〜2026-09-17|{'FREE_UNVERIFIED': 547, 'DELAYED': 23}|
|xli|XLI|yahoo_finance|America/New_York / 16:00|available|781|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 781}|
|ewy|EWY|yahoo_finance|America/New_York / 16:00|available|587|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 587}|
|toyota_adr|TM|yahoo_finance|America/New_York / 16:00|available|586|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 586}|
|honda_adr|HMC|yahoo_finance|America/New_York / 16:00|available|598|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 598}|
|xlf|XLF|yahoo_finance|America/New_York / 16:00|available|779|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 779}|
|kre|KRE|yahoo_finance|America/New_York / 16:00|available|809|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 809}|
|mufg_adr|MUFG|yahoo_finance|America/New_York / 16:00|available|586|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 586}|
|smfg_adr|SMFG|yahoo_finance|America/New_York / 16:00|available|597|2024-06-03〜2026-09-17|{'FREE_UNVERIFIED': 597}|

## 未導入候補

BCI/BPI/実BDI・Iron Ore・JKM LNG・原料炭は安定した利用許諾付きhistorical availability未確認。BDRYはBDIそのものではない。Maruti、Nifty Auto、INR系FX、RBOB/Heating Oil/crack spread、保険ETF、その他ADR/海外同業株は研究候補で、本番採用なし。米国株通常closeは04:00 JST（DST）/05:00 JST（標準時）。NSE通常closeは19:00 JST。市場closeとprovider available_timestampは別々に検証する。

詳細な経済仮説と公式情報の出典は同日の3業種別driver researchを参照。ニュースNLPはlicenseと時点付きhistorical archiveが未確立のためRESEARCH_ONLY。

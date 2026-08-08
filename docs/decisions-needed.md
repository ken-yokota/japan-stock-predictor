# 実装継続前の確認事項

Phase 1はYahoo primary、U.S. Treasury official、EODHD Free optional fallbackで構成し、
利用できない値は`MISSING`としてfail closedにしています。以下はPhase 2以降へ進む前、
または外部公開前に必要な決定です。

## データ利用と公開範囲

1. **Yahoo Financeデータの利用範囲**
   - `yfinance`はYahoo非公式・非提携のOSSで、personal use、research / educational
     purposes向けと明記しています。
   - 現状は本人の私的研究用途を前提とします。公開Streamlit、第三者宛メール、法人・
     業務利用、保存データや派生値の再配布を行う前にYahooの規約と権利を確認します。
   - 根拠: [yfinance README](https://github.com/ranaroussi/yfinance#readme)

2. **EODHD Free fallbackを本番で有効にするか**
   - 無料プランは公式上20 calls/day、EOD履歴は過去1年までです。本アプリは1実行5
     callsで止めます。
   - API keyを設定しない場合もYahoo + Treasuryで動作し、fallbackは無効になります。
   - EODHDデータも公開・再配布前に
     [利用条件](https://eodhd.com/financial-apis/terms-conditions)を確認します。

3. **Dashboardの公開方式**
   - データ利用許諾が確定するまでは本人限定または非公開とするか。
   - 公開する場合の認証、利用者、保存期間、表示可能なraw / derived項目を決めます。

## 未解決データ

4. **Iron Ore**
   - 唯一未解決の必須指標です。
   - 08:30以前のevent / publication時刻、訂正履歴、保存・派生・表示権を確認できる
     Providerを選ぶか、初期モデルから明示的に除外する必要があります。

5. **Baltic指数**
   - Baltic Dry Indexは現在BDRY ETFを代理特徴量として使用し、直接BDIではありません。
   - 直接BDI、任意のCapesize / Panamaxを使う場合は、licensed sourceとPIT公表時刻を
     確認します。

6. **Yahooのcontinuous futures**
   - `NIY=F`、`ES=F`、`NQ=F`、`GC=F`、`HG=F`、`CL=F`、`BZ=F`、`NG=F`は連続先物
     symbolです。限月ロール、価格調整、実取引可能価格との差をどのように扱うか決めます。

## 時刻と運用

7. 予測cutoffは対象日 `08:30 Asia/Tokyo` に固定し、遅延実行でも現在時刻へ動かさない
   方針を維持するか。
8. 08:20取得後に08:29のsnapshot再取得を行うか。Yahooには鮮度・到着SLAがないため、
   失敗時は`MISSING` / `INSUFFICIENT_DATA`とするか。
9. GitHub Actionsのcronは開始時刻を保証しません。08:45メールを厳格なSLAにする場合、
   Render等のscheduler / workerを使うか。
10. 15:45の当日Open / Closeについて、Yahooで当日EODが完成した保証はありません。
    未確定を`PENDING`として再試行する時刻、または翌朝確定へ変更するか。
11. U.S. Treasury XMLは公式値ですが、各履歴行の公表timestampを返しません。本実装の
    18:00 ET availabilityは保守的なschedule estimateとして扱い、運用当日は
    `first_observed_at <= 08:30 JST`と最新米国sessionを追加検査する方針でよいか。

## DB・再現性

12. 実PostgreSQLの`DATABASE_URL`を用意し、`0001_phase1`から
    `0002_free_provider`へのupgrade、downgrade、revision / upsert、同時実行を検証する。
13. 履歴バックフィルのYahoo値は、当時の公表時刻を証明できず
    `FREE_UNVERIFIED`です。研究用walk-forwardで許容するか、prospectiveに収集した値だけ
    を厳格PIT評価へ使用するか。
14. model runで使用したProvider選択、raw row ID、data hash、feature version、code SHAの
    保存期間を決める。

## 学習・バックテスト

15. 120学習日＋20日warm-upに加え、walk-forward評価を何営業日分保持するか。
16. targetは同日raw Open / Close、複数日特徴量はAdjusted Closeとするか。
17. `INSUFFICIENT_DATA`の欠損率、必須特徴量、最大staleness、最低学習行数。
18. 手数料・スリッページのbps、片道 / 往復、日本株100株単元を使うか。
19. Expectancyを
    `WinRate × AverageWin - LossRate × abs(AverageLoss)`と解釈するか。
20. Confidence、Prediction Interval、Readabilityの正規化とsample penalty式。
21. 予測差額の基準を前日Closeにするか、09:00実Open取得後の再計算jobを追加するか。
22. 1銘柄100万円を各銘柄独立とするか、Top Nで総資金を制限するか。

## 通知

23. `EMAIL_FROM`のResend検証済みdomain、`EMAIL_TO`、`APP_URL`。
24. BUY候補0件、Provider fallback使用、必須source欠損時のメール表示と送信可否。

## 現在採用している安全側の既定

- 未確認symbolや架空値を使用しない。
- 08:30より1マイクロ秒でも後に利用可能・取得完了となった値を除外する。
- snapshotのsource timestamp、first observation、retrievalをすべて検査する。
- Provider候補は品質・PIT・coverage gate後に系列単位で選び、行単位で混ぜない。
- 後日訂正版を過去cutoffへ遡及させない。
- `OFFICIAL`、`FREE_UNVERIFIED`等の品質と、`FRESH` / `STALE`、
  `PRIMARY` / `FALLBACK`を別に記録する。
- 欠損率と売買コストは未確認のため`null`。BUYや損益をまだ生成しない。
- 商社専用指標は仕様にないため、当面は共通指標だけとする。

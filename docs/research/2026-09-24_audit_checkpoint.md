# 日本株予測システム全面監査・中間チェックポイント

確認時点: 2026-09-24 21:19 JST。これは全8工程の中間記録であり、本番Cloudの稼働版と20新営業日の前向き評価を完了したという意味ではない。

| 工程 | 現在位置 | 実測と残件 |
| --- | --- | --- |
| 1/8 Git履歴・旧資産 | 基準監査完了 | [Git監査](2026-09-19_git_audit.json)で履歴と旧資産を記録。 |
| 2/8 本番コード・DB・運用 | 基準監査完了、運用差分は継続 | [DB監査](2026-09-19_database_audit.json)と[公開履歴の整合性調査](2026-09-23_live_history_integrity.md)を作成。Cloud Historyの稼働版と修正後集計の整合は未確認。 |
| 3/8 22銘柄の指標・IR・ニュース | 基準調査完了 | [銘柄別調査](2026-09-19_ticker_driver_research.md)、[海運・エネルギー](2026-09-19_shipping_energy_driver_research.md)、[自動車・商社](2026-09-19_autos_trading_driver_research.md)、[金融](2026-09-19_financials_driver_research.md)を記録。候補指標は前向き証拠なしに本番採用していない。 |
| 4/8 resolver・校正・説明 | 実装をmainへ反映 | 銘柄別resolver、校正表示と説明情報の修正を反映。[当日説明の完全性](2026-09-24_daily_explanation_integrity.md)を検証。予測モデルの昇格はしていない。 |
| 5/8 時系列OOS・費用・比較 | 歴史比較まで実施、前向き比較は継続 | 固定Huber全腕比較は[有効予測0で集計不成立](2026-09-24_robust_candidate_first_run.md)。事後探索[Extra Trees結果](2026-09-24_extra_trees_postfailure_result.md)は22銘柄・49日・共通1,036ペア。現行よりMAEは小さいがゼロ予測より大きく、20bp費用で期待値は負。推定バックフィル・事後選択のため昇格なし。 |
| 6/8 テスト・画面・メール | コードCI合格、配信とCloud検証は継続 | PR #17–#20のPR CIは各6項目合格、PR #20のmerge後CIは3項目合格。9/24朝メールは送信工程のprovider成功・message IDを確認。夕方結果メールは[実行](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35978824789)が成功し、DB送信記録も09:03:59 UTCにSENT。いずれも受信箱への到達は未確認。 |
| 7/8 安全なデプロイ・本番画面 | 未完了 | 公開Cloud Historyは11:21 JSTに全期間972件。読取専用DB集計は終値前528適格/24日、終値後550適格/25日で、表示と集計の対象条件は異なる。[Cloud差分](2026-09-24_cloud_deployment_gap.md)を記録。管理画面は未サインインで稼働ブランチ・コミット・再ビルドを確認できていない。 |
| 8/8 結果・forward・rollback | 登録済み、初日確定、残り19日 | [登録](2026-09-20_forward_registration.json)の設定6ファイルのSHA-256は一致。初日9/24は22銘柄CLEAN、11 BUY、終値後22件FINAL・0 pending。[初日の確定記録](2026-09-24_forward_day1.md)ではBUY 11件全て下落、ゼロ費用の紙上損益-138,950円。21/22銘柄が下落した1日だけでは持続的な性能を判断できない。20日目の最短日は10/22。 |

9/24の終値処理は定期実行が16:14 JSTまで見えず、日付固定の[手動workflow](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35968542157)で22件を確定した。終値後の[読取専用集計](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35969724997)は、登録設定ハッシュに紐づく1日・22予測・11紙上BUYを示した。21:10 JST起動の[遅延した定期終値処理](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35997356285)もSUCCESS・22件確定・訂正0件。直後の読取専用DB照合は同日の最新READYセットで22予測・11 BUY・22 FINAL・0 PENDING・結果バージョン最大1・11 FINAL紙上取引・損益-138,950円を確認した。これらはDB上の実行・集計を裏付けるが、Cloudの稼働版やメール受信を証明しない。

夕方メールの「直近10営業日」には登録前のDB履歴が含まれる。PR [#20](https://github.com/ken-yokota/japan-stock-predictor/pull/20)で本文とHTMLに前向き登録集計との区別を明記した。9/24の送信前dry-runは当日11 BUY・0勝11敗・紙上損益-138,950円、要確認4件を示した。要確認には外部系列14件の取得不完全、予測注記2件、研究成果4件の無効化が含まれる。DB履歴10日の累積値を新forwardの成果として引用しない。

[既存のデプロイ検証](2026-09-19_deployment_validation.md)には追加型migration 0007とアプリのrollback条件を記録している。ただしCloud管理画面に入れないため、現在の稼働版からの実際のrollback操作は未検証である。

次の判定は、夕方メールの送信と受信の区別、Cloud管理画面での稼働版確認、残り19営業日の欠測を含む観測、20日到達後の同一日付・同一設定での誤差・方向・校正・費用別取引成績の評価による。モデル・指標・閾値は前向き期間中に変更しない。

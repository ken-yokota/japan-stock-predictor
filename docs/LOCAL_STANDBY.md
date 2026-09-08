# ローカル待機運用（Neonの転送枠が尽きている間）

> **状態: 休止中（2026-09-07 に解除）。**
> Neonの転送枠は9/2〜9/7の間に回復し、GitHub Actions と Streamlit Cloud は
> 通常構成に戻した。launchdのジョブは停止・削除済みで、`AUTOMATION_ENABLED`
> は `true` に戻っている。この文書は次に同じことが起きたときの手順として
> 残してある。**そのまま再実行する前に、下の「積み残し」を読むこと。**
>
> ## 積み残し（2026-09-07 時点で未修正）
>
> 待機構成には、実運用で判明した欠陥が3つある。再開するなら先に直すこと。
>
> 1. **`alembic upgrade head` を実行していなかった。** 本番workflowは毎回
>    実行している。これを欠いたためローカルDBのスキーマが古く、9/2〜9/7の
>    予測は毎朝 `column predictions.return_distribution does not exist` で
>    全滅した（`prediction_sets` は FAILED、予測0件）。
> 2. **snapshotステージに `DATABASE_URL` が渡っていなかった。**
>    `export_dashboard_snapshot` は `.env` を読まないため、毎回
>    `DATABASE_URL is not set` で失敗していた。
> 3. **夕方の工程が存在しない。** `close_update`（15:45）と
>    `daily_summary`（17:00）に相当するジョブを作っていなかったので、
>    大引け後の実績メールは待機中ずっと送られなかった。

2026-08-30にNeonの月間データ転送枠を使い切り、**接続そのものが拒否される**状態に
なった。GitHub Actionsの朝の一連（prefetch → predict → email → snapshot）は
すべてDBに触るため全滅する。この文書は、その間だけこのMacで同じ処理を回すための
構成を書く。

Neonが復帰したら「戻し方」の節まで飛んでよい。

## 何が変わって、何が変わっていないか

| 要素 | 通常 | 待機中 |
|---|---|---|
| スケジューラ | GitHub Actions cron | **launchd（このMac）** |
| データベース | Neon | **ローカルPostgres 16** |
| 実行スクリプト | `scripts/*.py` | 同じ |
| メール | Gmail SMTP | 同じ |
| snapshotの公開先 | `snapshot` ブランチ | 同じ |
| Streamlit Cloud | Neonを直接読む | **更新されない**（後述） |

スクリプトもメール経路も公開先も変えていない。差し替えたのは
**スケジューラとデータベースの2つだけ**である。

## 時刻表

launchdはこのMacのローカル時刻（JST）で発火する。本番のcronと同じ時刻に合わせてある。

| ジョブ | JST | 中身 |
|---|---|---|
| `com.yokotaken.jsp.prefetch` | 06:10 / 06:40 / 07:10 / 07:25 / 07:50 | `scripts.prefetch_morning_data` |
| `com.yokotaken.jsp.predict` | 08:20 / 08:30 | `scripts.run_morning_prediction` |
| `com.yokotaken.jsp.snapshot` | 08:36 | `scripts.export_dashboard_snapshot` → `snapshot`ブランチ |
| `com.yokotaken.jsp.email_defer` | 08:45 | `scripts.send_morning_email --defer-missing` |
| `com.yokotaken.jsp.email` | 08:52 | `scripts.send_morning_email` |

prefetchの試行が本番より多いのは、**Yahooが前営業日の日本株終値を確定する時刻が
読めない**ためである。2026-09-02の02:46時点で9/1の終値はまだNaNだった。1本でも
欠けると `provider_router` の100%カバレッジ要求で66系列すべてが却下される。

`email_defer` と `email` を分けているのは本番の挙動に合わせるためで、最初の試行は
予測が無くても黙り、最後の試行だけが「予測なし」通知を出して非ゼロ終了する。

## 失敗したとき

各ジョブは `local_logs/<日付>_<ステージ>.log` に追記し、**失敗すると
`scripts.send_progress_report` でメールを送る**。沈黙は成功ではない。

```bash
launchctl list | grep jsp                     # 稼働確認
tail -50 local_logs/$(date +%F)_predict.log   # その日のログ
```

手で回すとき:

```bash
./scripts/local_cron/run_stage.sh prefetch
./scripts/local_cron/run_stage.sh predict
./scripts/local_cron/run_stage.sh snapshot
./scripts/local_cron/run_stage.sh email
```

## snapshotの公開には歯止めがある

`scripts/local_cron/publish_snapshot.sh` は **予測が0件のsnapshotを公開しない**。

Actions側の同名workflowにはこの歯止めが無く、8/31以降26回にわたり
`0 tickers / predictions=UNAVAILABLE` を正常な公開版に force-push で上書きし続けた。
空のページより古いページのほうがましなので、ここでは押し戻す。

## Streamlit Cloudは更新されない

`dashboard/ui.py` は `os.environ["DATABASE_URL"]` を読んでDBに直接つなぐ。
Streamlit CloudのsecretはNeonを指しており、そのNeonが落ちている。
ローカルPostgresはインターネットから見えないので、**待機中はクラウドの
ダッシュボードだけ復旧できない**。選択肢は2つ:

1. Streamlitのsecretを、到達可能な別のPostgresに向ける（操作者の作業）。
   同じ構成のままホストだけ変わる。
2. `dashboard/ui.py` に「DBが読めないときは公開済みsnapshot JSONを読む」
   フォールバックを足す。構成は変わるが操作者の作業は要らない。

`snapshot` ブランチの `dashboard_snapshot.json` は待機中も正しく更新されるので、
公開ミラーの中身自体は正しい。

## 戻し方

```bash
gh variable set AUTOMATION_ENABLED --body true          # Actionsを再開
for n in prefetch predict snapshot email_defer email; do
  launchctl bootout gui/$(id -u)/com.yokotaken.jsp.$n
  rm ~/Library/LaunchAgents/com.yokotaken.jsp.$n.plist
done
```

再開前に、ローカルPostgresに溜まった予測をNeonへ移すか捨てるかを決めること。
移すなら復帰当日にまず `pg_dump` を取る（転送枠が尽きると `pg_dump` すら
通らない、というのが今回の教訓である）。

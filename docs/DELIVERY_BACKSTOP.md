# 配信のバックストップ（GitHubがスケジュールを落としたとき）

## 何が起きたか

2026-09-08、GitHub Actions が**夕方の予定実行を1本も起動しませんでした**。

| workflow | cron (UTC) | 起動 |
|---|---|---|
| close_update | 06:45 / 06:55 / 07:10 | **なし** |
| daily_summary | 08:00 / 08:20 / 08:40 | **なし** |

同じ日の朝は起動しましたが、いずれも**1.5〜3.6時間の遅延**でした（prefetch 2時間、prediction 1時間50分、watchdog 3時間38分）。workflowはすべて `active`、`AUTOMATION_ENABLED` も `true` で、設定側に問題はありません。

GitHubのスケジュール実行は**ベストエフォート**で、起動しないことがあります。`daily_summary.yml` のコメントにも 2026-08-12 に朝のcronが起動しなかった記録が残っています。**cronの本数を増やしても、1本も起動しない日には効きません。**

## 対策

同じスケジューラに頼らない**2本目の起動経路**を、常時起動しているこのMacに置きました。

| ジョブ | JST | 中身 |
|---|---|---|
| `com.yokotaken.jsp.ensure-morning` | 09:10 | 朝の予測とメールが出ているか確認し、無ければ `morning_kick` を起動 |
| `com.yokotaken.jsp.ensure-evening` | 17:55 / 18:45 | 実績確定と大引け後メールを確認し、無ければ `close_update` → `daily_summary` を起動 |

GitHubが主、これは**補助**です。欠けているときだけ動き、起動するのはcronが動かすはずだった同じworkflowです。

## 二重送信しない理由

- 送信は `email_logs` の日付キーで冪等です。遅れて起動したcronと競合しても2通にはなりません
- 判定は監視スクリプト `scripts/verify_daily_delivery.py` の結果を使います。祝日や「まだ時刻前」は失敗として扱いません
- `AUTOMATION_ENABLED` が false でも**復旧しません**。意図的に止めた運用を上書きしないためです

判定ロジックは [scripts/delivery_backstop.py](../scripts/delivery_backstop.py) にあり、テストで固定してあります。

## 動いたときは必ず通知します

黙って直すと「GitHub側が落ちている」という事実が見えなくなるので、**代替起動したときは進捗メールで報告します**。このメールが届いたら、GitHubのスケジュールがその窓を落としたということです。

## 確認とメンテナンス

```bash
launchctl list | grep jsp                                   # 稼働確認
tail -50 local_logs/$(date +%F)_ensure_evening.log          # その日の判定
./scripts/local_cron/ensure_delivery.sh evening             # 手で確認（冪等）
```

停止するとき:

```bash
for n in ensure-morning ensure-evening; do
  launchctl bootout gui/$(id -u)/com.yokotaken.jsp.$n
  rm ~/Library/LaunchAgents/com.yokotaken.jsp.$n.plist
done
```

## 限界

**このMacが起動していないと動きません。** スリープは無効化済み（`pmset` の `sleep 0`）ですが、電源が落ちていればGitHubが落とした窓はそのまま落ちます。その場合でも現状より悪くはなりません。

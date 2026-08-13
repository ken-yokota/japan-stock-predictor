# 外部スケジューラで朝を時間どおりに動かす

## なぜ必要か

GitHub Actions は scheduled workflow の実行時刻を保証しません。実測です。

| 日付 | ジョブ | 予定(JST) | 実際(JST) | 遅延 |
|---|---|---|---|---|
| 2026-08-12 | 予測 | 08:10 | 08:55 | +45分 |
| 2026-08-12 | メール | 08:45 | 09:42 | +57分 |
| 2026-08-13 | 予測 | 08:10 | 08:55 | +45分 |
| 2026-08-13 | メール | 08:45 | 09:44 | +59分 |
| 2026-08-13 | watchdog | 09:10 | 11:42 | +152分 |

2026-08-13 のメールは 3 tick（08:45 / 08:50 / 08:55）が **09:44:14 / 09:44:14 / 09:45:24 に一斉に実行**されました。個々の tick が不運だったのではなく、**キューが詰まって同時に流れた**形です。

したがって **cron を増やしても早くなりません。**同じ待ち行列に並ぶだけです。GitHub の外から叩く以外に手はありません。

## 何が起きるようになるか

現在、予測は 08:55 に出て、メールは寄り付き（09:00）の 44 分後に届いています。外部トリガーを入れると **08:05 に確実に起動し、メールは 08:30 前後に届きます。**

## 仕組み

外部の無料 cron サービスが、1日1回 GitHub の API を叩くだけです。

```
外部cron (08:05 JST)
  → GitHub API: morning_kick.yml を dispatch
      → prefetch → 予測 → メール → 欠損監査
```

**既存の cron はそのまま残します。**すべての処理が冪等なので、両方走っても二重にはなりません。外部が失敗すれば GitHub の cron が（遅れて）拾い、GitHub が詰まれば外部が定刻に動かします。

## あなたがやること

### 1. GitHub でトークンを作る

1. https://github.com/settings/personal-access-tokens/new を開く
2. **Token name**: `japan-stock-predictor morning kick`
3. **Expiration**: 1 year
4. **Repository access**: Only select repositories → `japan-stock-predictor`
5. **Permissions** → Repository permissions → **Actions** を **Read and write** に
6. Generate token → **表示された文字列をコピー**

> トークンはこのチャットに貼らないでください。次の手順で外部サービスに直接貼ります。

### 2. cron-job.org に登録する

1. https://cron-job.org/en/ で無料アカウントを作成
2. **Create cronjob**
3. **Title**: `japan-stock-predictor morning`
4. **URL**:
   ```
   https://api.github.com/repos/ken-yokota/japan-stock-predictor/actions/workflows/morning_kick.yml/dispatches
   ```
5. **Schedule**: 毎日 **08:05**（タイムゾーンを Asia/Tokyo に設定）、曜日は月〜金
6. **Advanced** → **Request method**: `POST`
7. **Headers** に4行追加：

   | Key | Value |
   |---|---|
   | `Accept` | `application/vnd.github+json` |
   | `Authorization` | `Bearer <手順1でコピーしたトークン>` |
   | `X-GitHub-Api-Version` | `2022-11-28` |
   | `Content-Type` | `application/json` |

   `Content-Type` を落とすと、本文がフォーム送信として送られて GitHub が
   読めず、422 で返ってきます。cron-job.org はこれを保存時に警告します。

8. **Request body**:
   ```json
   {"ref":"main"}
   ```
9. Save

### 3. 動作確認

cron-job.org の **Test run** を押してください。数秒後に
https://github.com/ken-yokota/japan-stock-predictor/actions/workflows/morning_kick.yml
に新しい実行が現れれば成功です。

成功したら「登録した」とだけ伝えてください。翌営業日から時刻どおりに動きます。

## うまくいかないとき

| 症状 | 原因 |
|---|---|
| HTTP 404 | トークンの Repository access がこのリポジトリを含んでいない |
| HTTP 403 | Actions 権限が Read and write になっていない |
| HTTP 422 | body の `ref` が `main` でない、または `Content-Type: application/json` が無い |
| 実行はされるが何も起きない | `AUTOMATION_ENABLED` が true か確認（現在は true） |

## 代替サービス

cron-job.org 以外でも、HTTP POST にヘッダとボディを付けられる無料スケジューラなら何でも使えます。

| サービス | 無料枠 | 備考 |
|---|---|---|
| cron-job.org | 無料・無制限 | 本手順で使用 |
| Cloudflare Workers Cron | 無料枠あり | コードを書く必要あり |
| UptimeRobot | 無料 | POSTのカスタムヘッダは有料プラン |

## 二重起動しても安全な理由

| 処理 | 二重防止 |
|---|---|
| 予測 | 同じ prediction_date・同じバージョンの公開済みセットがあれば再計算せず返す |
| メール | `email_logs` の冪等キーで、同じ公開に対して1回だけ送る |
| prefetch | 保存済みカバレッジがあれば取得自体を省略する |
| 引け更新 | 同日再実行で取引が二重に作られない |

# 外部サービス設定チェックリスト

更新日: 2026-08-08

残り6つのUser Actionを、上から順に実行できる形にまとめています。

## 最重要: 秘密情報の渡し方

**このリポジトリの作業で、パスワード・APIキー・接続文字列をチャットや
Issue、commit、Actionsのログへ貼らないでください。**

秘密情報の置き場所は次の3か所だけです。いずれもあなたが直接入力します。

| 置き場所 | 用途 | 誰が入力するか |
|---|---|---|
| ローカルの `.env` | 手元での実行 | あなた（`.gitignore`済みでcommitされません） |
| GitHub → Settings → Secrets and variables → Actions | 自動実行 | あなた |
| Streamlit Community Cloud → App settings → Secrets | ダッシュボード | あなた |

支援を求めるときに必要なのは、**秘密情報ではなく症状**です。具体的には
エラーメッセージの種類、失敗したステップ名、`daily_runs` のstatusなどです。
接続文字列そのものは不要です。

このリポジトリ: <https://github.com/ken-yokota/japan-stock-predictor>

---

## 1. Neon（無料PostgreSQL）

作業URL: <https://neon.tech>

1. GitHubアカウントでサインアップする。
2. プロジェクトを作成する。リージョンは `AWS ap-northeast-1 (Tokyo)` が近くて有利です。
3. データベース名は既定の `neondb` のままで構いません。
4. Dashboard の **Connection string** をコピーする。`postgresql://` で始まる1行です。

**書き換えは不要です。そのまま貼ってください。** `database/connection.py` の
`normalize_database_url` が、`postgresql://` と `postgres://` を psycopg 3 用の
`postgresql+psycopg://` へ自動変換します。`.env`、GitHub Secrets、Streamlit Secrets の
どこに入れても同じ処理を通ります。

```text
Neonが表示する形:     postgresql://USER:PASSWORD@HOST/neondb?sslmode=require
アプリ内部での扱い:   postgresql+psycopg://USER:PASSWORD@HOST/neondb?sslmode=require
```

`sslmode=require` は消さないでください。

コピーした文字列を `.env` の `DATABASE_URL` に入れ、スキーマを作成します。

```bash
cd /Users/yokotaken/Desktop/japan-stock-predictor
# .env の DATABASE_URL を Neon の値に書き換えてから:
set -a; . ./.env; set +a
.venv/bin/alembic upgrade head
.venv/bin/alembic current
```

`0003_prediction_pipeline` まで進めば成功です。

**チェック**: Neon の Tables 画面に 20 テーブルが見えること。

---

## 2. Gmail SMTP（App Password）

作業URL: <https://myaccount.google.com/security>

1. **2段階認証プロセス** を有効にする。これを先に済ませないと次に進めません。
2. <https://myaccount.google.com/apppasswords> を開く。
3. アプリ名に `japan-stock-predictor` などと入力して生成する。
4. 表示される **16桁の英数字** を控える。この画面を閉じると二度と表示されません。

通常のGoogleアカウントのパスワードは使いません。App Password だけを使います。

`.env` に入れる値:

```dotenv
EMAIL_PROVIDER=gmail_smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=＜あなたのGmailアドレス＞
SMTP_PASSWORD=＜16桁のApp Password。スペースは詰める＞
EMAIL_FROM=＜通常SMTP_USERNAMEと同じ＞
EMAIL_TO=＜スマホで受信したいアドレス＞
```

**チェック**: 外部送信せずに本文だけ確認する。

```bash
.venv/bin/python -m cli send-email --prediction-date 2026-08-10 --dry-run
```

このコマンドは送信もしませんし、email log も消費しません。予測がまだ無い日付では
「該当するprediction setがない」と出るのが正常です。

---

## 3. Streamlit Community Cloud（スマホから常時アクセス）

作業URL: <https://share.streamlit.io>

1. GitHubアカウントで **Sign in** する。
2. **Create app** → **Deploy a public app from GitHub** を選ぶ。
3. 設定値を次のとおり入力する。

   | 項目 | 値 |
   |---|---|
   | Repository | `ken-yokota/japan-stock-predictor` |
   | Branch | `main` |
   | Main file path | `app.py` |
   | Python version | `3.12` |

4. **Advanced settings → Secrets** に、`DATABASE_URL` の1行だけを貼る。

   ```toml
   DATABASE_URL = "postgresql://USER:PASSWORD@HOST/neondb?sslmode=require"
   ```

   TOML なので値は必ずダブルクォートで囲みます。ここでも書き換えは不要です。

   ダッシュボードはDBを読むだけなので、Provider keyもSMTP情報も**渡しません**。
   渡すと、漏れたときの被害が広がるだけで、得るものがありません。

5. Deploy する。`https://＜アプリ名＞.streamlit.app` が発行されます。
6. そのURLをスマホで開き、共有ボタンから**ホーム画面に追加**する。アプリのように起動できます。

### 公開範囲について

private リポジトリからデプロイした場合でも、**発行されたURLを知っている人は誰でも
開けます**。認証はかかりません。次の2点に注意してください。

- Yahoo/yfinance のデータを第三者へ再配布してよいかは未確認です（`KNOWN_ISSUES.md`）。
  当面は本人利用に留め、URLを公開の場に貼らないでください。
- Streamlit の設定で app を private にできる場合は、そちらを選んでください。

デプロイ後、発行URLを GitHub Secrets の `APP_URL` に登録します。メール本文の
リンク先になります。

---

## 4. GitHub Secrets / Variables の登録

作業URL: <https://github.com/ken-yokota/japan-stock-predictor/settings/secrets/actions>

**Secrets**（New repository secret から1つずつ）:

| 名前 | 中身 |
|---|---|
| `DATABASE_URL` | Neonの接続文字列（コピーしたまま。書き換え不要） |
| `SMTP_USERNAME` | Gmailアドレス |
| `SMTP_PASSWORD` | 16桁のApp Password |
| `EMAIL_FROM` | 送信元アドレス |
| `EMAIL_TO` | 受信アドレス |
| `APP_URL` | Streamlitの公開URL |
| `EODHD_API_KEY` | 任意。設定しなければfallbackが無効になるだけです |

**Variables**（<https://github.com/ken-yokota/japan-stock-predictor/settings/variables/actions>）:

| 名前 | 値 | いつ設定するか |
|---|---|---|
| `AUTOMATION_ENABLED` | `true` | **手順6を終えるまで設定しないでください** |

これが安全ゲートです。設定するまで定時実行は動かず、手動実行だけができます。

---

## 5. GitHub Actions の手動 dry-run

作業URL: <https://github.com/ken-yokota/japan-stock-predictor/actions>

この順番で確認します。前が緑になってから次へ進んでください。

1. **CI** が緑であること。
2. **Morning prediction** を `Run workflow` から手動実行する。予測日には
   実在するJPX営業日を入れる。
3. Streamlit の Today と System Status を開き、DBに書かれたことを確認する。
4. **Morning email** を `dry_run = true` で実行する。本文が生成されるだけで、
   送信もemail logのclaimもしません。
5. 問題なければ dry-run を外して1回だけ実行し、自分宛に1通届くことを確認する。

**確認する点**: Actionsのログには秘密情報が出ません。失敗したときは、
ログ本文を貼るのではなく、失敗したステップ名とエラーの種類を見てください。

履歴データが無い状態では全銘柄 `INSUFFICIENT_DATA` になります。これは異常では
ありません。手順6の bootstrap が先に必要です。

### 手動実行の日付は「これから来る営業日」を指定する

**過去の日付を指定すると必ず失敗します。**

```text
ValueError: raw input was not observed by the prediction cutoff
```

これは不具合ではなく、Look-Ahead防止機能が正しく働いた結果です。朝のpipelineは
「その日の08:30より前に実際に観測したデータ」しか特徴量に使いません。過去日を後から
指定すると、データを取得したのはその締切より後なので、システムが使用を拒否します。

| 指定する日付 | 結果 |
|---|---|
| 過去の営業日 | ❌ `raw input was not observed by the prediction cutoff` |
| 当日（営業日） | ✅ 正常。本番と同じ動き |
| 当日（休場） | ⏭ `SKIPPED`。これも正常 |
| これから来る営業日 | ✅ 正常。テストに使える |

過去期間の成績を見たい場合は朝のpipelineではなく、推定PITを使う
`python -m cli walk-forward` または `python -m cli week-test` を使ってください。

---

## 6. 履歴の投入と有効性の判断

```bash
set -a; . ./.env; set +a

# 2〜3年分。Providerが返せる範囲までしか入りません。
.venv/bin/python -m cli bootstrap-history \
  --from-date 2023-08-01 --to-date 2026-08-07

# 過去データからOOS予測をまとめて作る
.venv/bin/python -m cli walk-forward \
  --from-date 2023-08-01 --to-date 2026-08-07
```

bootstrap は時間がかかります。無料Providerのレート制限に当たるため、
途中で失敗した銘柄は記録され、未来値では埋められません。

その後、実運用の close 更新を実データで手動確認します。

```bash
.venv/bin/python -m cli close --prediction-date ＜実在の営業日＞
```

ここまで問題が無ければ、`AUTOMATION_ENABLED=true` を設定して定時実行を開始します。

### 有効性を判断する基準

Backtest 画面で次を**この順番で**見てください。数字の良さより先に、母数を見ます。

1. **Trades / Sample**: 20件未満は `LOW_SAMPLE` です。この段階では
   Profit Factor がいくら高くても判断材料になりません。
2. **Max Drawdown**: 最大でどれだけ沈んだか。実際に耐えられる幅ですか。
3. **Profit Factor**: 1.0 が損益分岐です。
4. **Expectancy**: BUYシグナル1回あたりの平均損益。
5. **Direction Accuracy**: 方向が当たった割合。50%付近なら情報がありません。

**注意**: Backtest画面で条件を何通りも試して、いちばん良い数字の条件を採用すると
selection bias が入ります。画面が試行回数を数えて警告します。実際の成績は、
そうやって選んだ数字より悪くなります。

また、無料データの履歴には「当時それが何時に入手できたか」の記録がありません。
過去のバックテストは実運用より良く見える可能性があります。日次運用を始めてから
貯まった分だけを別に評価するのが、いちばん信頼できる判断材料です。

---

## 進捗メモ

- [ ] 1. Neon 作成、`alembic upgrade head` 成功
- [ ] 2. Gmail App Password 作成、dry-run で本文確認
- [ ] 3. Streamlit デプロイ、スマホのホーム画面に追加
- [ ] 4. GitHub Secrets 登録（`AUTOMATION_ENABLED` はまだ）
- [ ] 5. Actions 手動 dry-run、実メール1通
- [ ] 6. bootstrap → close 手動確認 → `AUTOMATION_ENABLED=true`

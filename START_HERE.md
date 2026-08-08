# あなたがやること

更新日: 2026-08-08

やることは **4つだけ** です。上から順にやってください。
1と2だけでも「スマホでいつでも見る」は達成できます。

すでに終わっていること（あなたの作業は不要）:
データベース構築、履歴データ取得（22銘柄・30指標・2年2か月分）、
モデル実装、ダッシュボード、テストページ、検証スクリプト。

---

## いま見られるもの（作業不要）

このMacで、すでに動いています。

- このMacから: <http://localhost:8501>
- 同じWi-Fiのスマホから: <http://192.168.3.11:8501>

「テスト」ページを開くと、**8/1〜8/7に買っていたらどうなっていたか**が出ます。

止めたいとき: ターミナルで `pkill -f streamlit`

---

## 1. Gmailのアプリパスワードを作る（5分）

**これをやると: 毎朝メールで予測が届くようになります。**

1. <https://myaccount.google.com/security> を開く
2. 「2段階認証プロセス」を**オン**にする（すでにオンならスキップ）
3. <https://myaccount.google.com/apppasswords> を開く
4. 名前を適当に入力（例: `stock`）して「作成」
5. **16桁の英数字**が出る。この画面を閉じると二度と見られないのでコピーする

コピーしたら、ターミナルで次を実行します。

```bash
cd ~/Desktop/japan-stock-predictor
open -e .env
```

テキストエディタが開くので、次の3行を書き換えて保存します。

```dotenv
SMTP_USERNAME=あなたのGmailアドレス
SMTP_PASSWORD=16桁のアプリパスワード（スペースは詰める）
EMAIL_FROM=あなたのGmailアドレス
```

`EMAIL_TO` はすでに `ky3141120@icloud.com` になっています。

**確認**: これで送れるようになります。

```bash
cd ~/Desktop/japan-stock-predictor
./scripts/send_test_result_email.sh
```

---

## 2. スマホからいつでも見られるようにする（10分）

**これをやると: 外出先でも、Macが消えていても見られます。**

いまのURLは自宅Wi-Fi内でしか使えません。外から見るにはネット上に置きます。

1. <https://share.streamlit.io> を開いて、GitHubアカウントで **Sign in**
2. **Create app** → **Deploy a public app from GitHub**
3. 次のとおり入力する

   | 項目 | 入れる値 |
   |---|---|
   | Repository | `ken-yokota/japan-stock-predictor` |
   | Branch | `main` |
   | Main file path | `app.py` |
   | Python version | `3.12` |

4. **Deploy** を押す
5. `https://なにか.streamlit.app` というURLが出る
6. スマホでそのURLを開き、共有ボタンから**ホーム画面に追加**

これで、アプリのように開けます。

**注意**: このURLを知っている人は誰でも開けます。SNSなどに貼らないでください。

### データも一緒に見たい場合

上の手順だけだと、画面は開きますが中身は空です。データはこのMacの中にあるためです。
中身も見せるにはネット上のデータベースが要ります。

1. <https://neon.tech> でGitHubアカウントでサインアップ
2. プロジェクトを作る（リージョンは `Tokyo` が速い）
3. **Connection string** をコピー（`postgresql://` で始まる1行。書き換え不要）
4. Streamlitの **App settings → Secrets** に貼る

   ```toml
   DATABASE_URL = "コピーした1行をここに"
   ```

急がないなら、ここは後回しで構いません。

---

## 3. 毎朝の自動実行を有効にする（あとで）

**これをやると: 毎朝8時20分に自動で予測が作られます。**

いますぐやる必要はありません。まず数日、手動で結果を見てからにしてください。

<https://github.com/ken-yokota/japan-stock-predictor/settings/secrets/actions>
を開き、**New repository secret** で1つずつ登録します。

| 名前 | 中身 |
|---|---|
| `DATABASE_URL` | Neonの接続文字列 |
| `SMTP_USERNAME` | Gmailアドレス |
| `SMTP_PASSWORD` | 16桁のアプリパスワード |
| `EMAIL_FROM` | Gmailアドレス |
| `EMAIL_TO` | `ky3141120@icloud.com` |
| `APP_URL` | StreamlitのURL |

そのあと
<https://github.com/ken-yokota/japan-stock-predictor/settings/variables/actions>
で `AUTOMATION_ENABLED` を `true` にすると自動実行が始まります。

**この最後の1つを設定するまで、自動実行は動きません。** 安全装置です。

---

## 4. 判断する（データが貯まってから）

いますることはありません。数週間後の話です。

「テスト」ページで、次を**この順番**で見てください。

1. **BUYシグナルの件数**。20件未満なら、勝率が何%でも判断材料になりません。
2. **全部買った場合との比較**。絞り込みが勝っていなければ、モデルの意味がありません。
3. **最大ドローダウン**。実際に耐えられる下げ幅ですか。

---

## 困ったときのコマンド

```bash
cd ~/Desktop/japan-stock-predictor

# ダッシュボードを起動する
./scripts/start_dashboard.sh --lan

# ダッシュボードを止める
pkill -f streamlit

# 検証をやり直す（日付は変えてよい）
.venv/bin/python -m cli week-test --from-date 2026-08-01 --to-date 2026-08-07

# 全部買った場合と比べる
.venv/bin/python -m cli buy-all --from-date 2026-08-01 --to-date 2026-08-07

# コマンド一覧
.venv/bin/python -m cli
```

---

## 補足

- 詳しい手順: [docs/SETUP_CHECKLIST.md](docs/SETUP_CHECKLIST.md)
- 画面の見方: [docs/DASHBOARD_GUIDE.md](docs/DASHBOARD_GUIDE.md)
- このMacへの変更と戻し方: [docs/CHANGE_LOG_LOCAL.md](docs/CHANGE_LOG_LOCAL.md)

**パスワードやAPIキーをチャットに貼らないでください。** `.env` と各サービスの
Secrets欄に、あなたが直接入力するだけで足ります。

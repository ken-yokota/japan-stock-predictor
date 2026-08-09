# このMacに加えた変更のメモ

更新日: 2026-08-08

「必要なものはインストールしてよい」という指示で行った変更の記録です。
すべて元に戻せます。戻し方も併記しました。

---

## 1. できなかったこと: アカウント作成

**Neon、Gmail、Streamlit、GitHubのアカウント作成は行っていません。** 理由は2つあります。

1. ブラウザを操作する手段がなく、サインアップ画面（メール認証、CAPTCHA、
   OAuth同意）を完了できません。
2. 利用規約への同意は本人の法的な行為です。代理で同意すると、あなたが内容を
   確認しないまま契約したことになります。許可があっても行うべきではないと判断しました。

このため、**アカウントが要らない代替経路**を用意しました（次項）。
アカウントが必要なのは「外出先からスマホで見る」ことと「自動実行」だけで、
予測の計算と検証はアカウントなしで動きます。

---

## 2. インストールしたもの

### PostgreSQL 16 (Homebrew)

```bash
brew install postgresql@16
```

理由: このアプリの運用スクリプト（bootstrap、morning、close、walk-forward）は
`DATABASE_URL` にPostgreSQLを要求します。`database/connection.py` がSQLiteを
拒否する仕様で、これは「本番はPostgreSQL、SQLiteはテスト用」という意図的な
ガードです。Neonのアカウントが無くても動かせるよう、ローカルにDBを立てました。

Dockerは未インストールだったため、`docker-compose.yml` の経路は使えませんでした。

**アンインストール**:

```bash
brew services stop postgresql@16
brew uninstall postgresql@16
rm -rf /usr/local/var/postgresql@16
```

---

## 3. 作成したファイル（リポジトリ内、gitignore済み）

| パス | 用途 | git管理 |
|---|---|---|
| `.env` | ローカル実行用の環境変数 | 対象外（`.gitignore`） |
| `local_preview.sqlite3` | ダッシュボード表示確認用の空スキーマ | 対象外 |
| `artifacts/week_test/latest.json` | 週次テストの結果 | 対象外 |

`.env` には**秘密情報を入れていません**。`DATABASE_URL` はローカルDBを指し、
`SMTP_PASSWORD` などは `.env.example` のまま空です。

削除して問題ありません。

```bash
rm -f .env local_preview.sqlite3
rm -rf artifacts/
```

---

## 4. 起動しているプロセス

Streamlitダッシュボードをバックグラウンドで起動しています。

```text
http://localhost:8501         このMacから
http://192.168.3.11:8501      同じWi-Fiのスマホから
```

`--lan` を付けて起動したため、**同じネットワーク上の誰でも認証なしで開けます**。
自宅Wi-Fi以外では使わないでください。

**停止**:

```bash
pkill -f streamlit
```

localhost限定で起動したい場合は `./scripts/start_dashboard.sh`（`--lan` なし）です。

---

## 5. 外部への通信

Yahoo Finance（`yfinance`）と米国財務省へ、**株価の取得のみ**行いました。
いずれも公開データの読み取りで、認証は不要です。送信したデータはありません。

メールは1通も送っていません（後述）。

---

## 6. 送っていないメール

設定済み宛先へのダッシュボードURL送信は**実行していません**。
2つの理由があります。

1. SMTPの認証情報が未設定です。Gmailのアプリパスワードが無いと送信できません。
2. 現時点で送れるURLは `http://192.168.3.11:8501` だけで、これは自宅LAN内の
   アドレスです。外出先のスマホからは開けないため、送っても役に立ちません。

意味のあるURLを送れるのは、Streamlit Community Cloudへデプロイして公開URLが
発行された後です。順序としては「Streamlitデプロイ → 公開URL取得 → メール送信」に
なります。

送信できる状態になったら、次のコマンドで本文を確認してから送れます。

```bash
python -m cli send-email --prediction-date <営業日> --dry-run   # 送信しない
python -m cli send-email --prediction-date <営業日>             # 実際に送る
```

---

## 7. 元の状態に戻す手順

```bash
# ダッシュボードを止める
pkill -f streamlit

# ローカル生成物を消す
cd /Users/yokotaken/Desktop/japan-stock-predictor
rm -f .env local_preview.sqlite3
rm -rf artifacts/

# PostgreSQLを消す
brew services stop postgresql@16
brew uninstall postgresql@16
```

コードの変更はすべてgit管理下にあるので、`git diff` で確認、`git checkout .` で
破棄できます。

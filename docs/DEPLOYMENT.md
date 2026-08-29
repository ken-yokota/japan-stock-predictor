# 無料構成デプロイ

更新日: 2026-08-08

## 構成

- Source / scheduler: private GitHub repository + GitHub Actions
- Database: Neon Free PostgreSQL（推奨。利用者が作成）
- Dashboard: Streamlit Community Cloud（利用者がdeploy）
- Email primary: Gmail SMTP + Google App Password
- Email optional: Resend API
- Market data: Yahoo / Treasury、任意でEODHD Free

外部account作成、billing/利用規約同意、secret登録はCodexから代行できないUser Actionである。

## 1. Neon

1. Neonでproject/databaseを作成する。
2. connection stringをpsycopg 3 URLとして取得する。
3. localで`DATABASE_URL`を一時設定し`alembic upgrade head`を実行する。
4. GitHub Actions repository secretとStreamlit secretの両方へ同じDB URLを登録する。

```dotenv
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require
```

接続数・storage・compute sleep等のFree tier制限はNeonの最新表示を確認する。URLをissue、commit、Actions logへ貼らない。

## 2. Gmail SMTP

Google accountで2段階認証を有効にし、App Passwordを作成する。通常のGoogle passwordは使わない。

GitHub repository secrets:

```text
SMTP_USERNAME       Gmail address
SMTP_PASSWORD       Google App Password
EMAIL_FROM          sender address（通常SMTP_USERNAMEと同じ）
EMAIL_TO            smartphoneで受信するaddress
APP_URL             Streamlitの公開URL
DATABASE_URL        Neon URL
```

workflowには`EMAIL_PROVIDER=gmail_smtp`, `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`を非秘密defaultとして設定する。Resendを使う場合だけ`EMAIL_PROVIDER=resend`と`RESEND_API_KEY`を登録する。

## 3. GitHub Actions

scheduled workflowをpushしただけでは本番実行を開始しない。安全gateとしてrepository variableを設定する。

```text
AUTOMATION_ENABLED=true
```

gateが実装されたworkflowでは、scheduled jobはこのvariableがtrueの場合だけ動き、`workflow_dispatch`は手動検証に使える。最初はvariableを設定せず、次の順で確認する。

1. CIがgreen。
2. Neon migration成功。
3. `morning_prediction`をdry-runまたは手動日付で実行。
4. DBとStreamlitを確認。
5. `morning_email`を`dry_run=true`で実行。renderのみでemail logはclaimしない。
6. 自分宛の実メールを1回だけ確認。
7. close scriptを実Yahoo/Neonで手動検証した後にautomation gateを有効化。

GitHub scheduled Actionsは指定時刻ちょうどの開始を保証しない。workflow cronはUTCで記述し、08:20/08:45 JSTは前日23:20/23:45 UTC、15:45 JSTは06:45 UTCへ換算している。JSTにDSTはない。遅延してもprediction cutoffは08:30 JST固定である。

## 4. Streamlit Community Cloud

1. private GitHub repositoryをStreamlitへ接続する。
2. entry pointを`app.py`、Pythonを3.14へ設定する。
3. Streamlit Secretsへ`DATABASE_URL`だけを登録する。DashboardはProvider keyやSMTP secretを必要としない。
4. deployし、DB health、System Status、Todayを確認する。
5. URLをGitHubの`APP_URL` secretへ入れる。

DashboardはDB read-only queryで、ページを開いてもProvider取得、モデルfit、メール送信を行わない。DB user自体も可能ならread-only roleにする。

## 5. 任意EODHD Free

`EODHD_API_KEY`をGitHub secretへ追加した場合だけfallback/validationで使う。Free quota保護は`config/settings.yaml`の5 calls/run。Primary大量取得にはしない。

## ローカル確認

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
docker compose up -d postgres
alembic upgrade head
pytest -q
ruff check .
streamlit run app.py
```

実secretを`.env.example`やGitに入れない。production migration前にbackup/branchを確認し、破壊的schema操作は別途reviewする。

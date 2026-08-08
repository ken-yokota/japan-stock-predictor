# Japan Stock Predictor

無料データだけを使い、08:30 JSTまでに利用可能だった情報から、日本株22銘柄の寄り付き→大引けリターンを研究するPython/Streamlitアプリです。銘柄別Ridge/Logistic、120 JPX営業日のwalk-forward、BUY判定、paper P/L、メール、Dashboardを一つのProvider-neutral architectureにまとめています。

売買を自動発注せず、予測・利益を保証しません。yfinanceは取引所公式APIではなく、価格・鮮度・継続提供・再配布権のSLAもありません。最終判断は利用者自身で行ってください。

## 無料構成

| 役割 | Provider / Service | 用途 |
|---|---|---|
| Market data primary | Yahoo Finance / `yfinance` | 日本株、米国株、ETF、ADR、指数、FX、先物、VIX |
| Treasury | U.S. Treasury | 2Y/10Y/30Y、10Y−2Y、1/3/5観測日変化 |
| Optional fallback | EODHD Free | symbol検証、EOD比較、quota内の一部fallback |
| Database | PostgreSQL（Neon Free想定） | raw revision、lineage、予測、actual、metric、email監査 |
| Scheduler | GitHub Actions | 08:20予測、08:45メール、15:45答え合わせ |
| Dashboard | Streamlit Community Cloud | DB read-only表示 |
| Email | Gmail SMTP primary / Resend optional | 個人向け朝メール |

EODHDはPrimaryではなく、`EODHD_API_KEY`も任意です。Provider具体実装はbusiness logicから分離され、将来EODHD PaidやTwelve Data等へ差し替えられます。

## 実装範囲

- strict YAML: 22銘柄、37指標、model/trading/provider/schedule
- Yahoo/Treasury/EODHD Free provider、timeout/retry、quota制限
- 08:30固定cutoff、snapshot freshness、EOD coverage、単一Provider選択
- provider/symbol/market/available/retrieved/quality/raw hash保存
- raw-row lineage付きPIT dataset、価格/Treasury特徴量
- training-only median imputer + StandardScaler、Ridge + Logistic、時系列CV
- strict one-step-ahead walk-forward、BUY、100株lot、両側cost paper simulation
- PF/Expectancy/Sharpe/Sortino/drawdown/correlation/Readability/Confidence
- 朝予測、寄り付き観測、大引けactual・paper P/L・metric、Gmail/Resend email service
- Streamlit 6画面（DB read-only）
- Alembic migrations、pytest、Ruff、mypy、GitHub Actions

外部サービスは未設定です。Neon、Streamlit、Gmail App Password、GitHub secretsを利用者が設定し、実営業日のend-to-end運用を確認するまではproduction完成とは扱いません。未完了と既知の制約は[実装レポート](docs/IMPLEMENTATION_REPORT.md)と[既知の問題](docs/KNOWN_ISSUES.md)を参照してください。

## Quick Start

Python 3.12以上を使います。

```bash
cd /Users/yokotaken/Desktop/japan-stock-predictor
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

ローカルPostgreSQL:

```bash
docker compose up -d postgres
alembic upgrade head
python -m data.fetch config-check
python -m pytest -q
ruff check .
```

`.env`へ実値を設定します。秘密値をcommitしないでください。

```dotenv
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require
EODHD_API_KEY=
EMAIL_PROVIDER=gmail_smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-gmail@example.com
SMTP_PASSWORD=google-app-password
EMAIL_FROM=your-gmail@example.com
EMAIL_TO=your-phone@example.com
RESEND_API_KEY=
APP_URL=http://localhost:8501
TIMEZONE=Asia/Tokyo
```

## Data / Pipeline CLI

全操作は `python -m cli <command>` からも呼べます。GitHub Actionsと手元で同じ
コードパスを使うための薄いdispatchで、業務ロジックは各scriptのままです。

```bash
python -m cli                    # コマンド一覧
python -m cli config-check       # 秘密情報なしで構成検証
python -m cli morning --prediction-date 2026-08-10
python -m cli send-email --prediction-date 2026-08-10 --dry-run
python -m cli close --prediction-date 2026-08-10
python -m cli dashboard          # Streamlitを起動
```

個別scriptを直接呼ぶ従来の形も変わらず使えます。

```bash
# 無料経路の構成と任意network検査
python -m scripts.phase0_data_feasibility
python -m scripts.phase0_data_feasibility --network

# Yahoo/EODHD symbol確認（EODHDはkey設定時だけ）
python -m data.fetch verify-yahoo
python -m data.fetch verify-eodhd

# 2〜3年を目標に初期履歴取得
python -m scripts.bootstrap_history \
  --from-date 2023-08-01 --to-date 2026-08-07

# 朝予測。予測日が省略ならJST today
python -m scripts.run_morning_prediction --prediction-date 2026-08-10

# 外部送信・email log claimなしでrenderを確認
python -m scripts.send_morning_email \
  --prediction-date 2026-08-10 --dry-run

# 任意: 寄り付き値をPENDING actualとして保存
python -m scripts.update_open --prediction-date 2026-08-10

# 大引け後にactual、paper trade、OOS metricを更新
python -m scripts.run_close_update --prediction-date 2026-08-10

# 過去raw DBからestimated-PIT walk-forwardを作りCSV/JSONへ出力
python -m scripts.run_walk_forward \
  --from-date 2023-08-01 --to-date 2026-08-07
```

walk-forward artifactは既定で`artifacts/backtest/`へ出力され、DBのlive metricとは混在させません。日付例はJPX営業日に置き換えてください。

## Dashboard

入口は `app.py` だけです。残り6画面は `pages/` から自動で読み込まれます。

```bash
./scripts/start_dashboard.sh          # http://localhost:8501
./scripts/start_dashboard.sh --lan    # 同一Wi-Fiのスマホからも見る
```

- Today: 今日の予測・BUY順位・warning・実績Open・Open基準の予測終値
- Stock Detail: 銘柄別prediction/actual/P&L/metric
- Factor Analysis: 標準化係数
- Sector Analysis: セクター単純集約
- Backtest: 保存済みOOS metricとpaper trade、および閾値・投資額・コスト・Top Nを
  変更した再計算
- System Status: DBに保存されたrun、Provider、鮮度、欠損

画面はDBだけを読み、表示中にYahoo等へ接続したりモデルを再学習したりしません。
Backtestの再計算も、保存済みのwalk-forward予測へ売買条件を再適用するだけで、
モデルの再学習は行いません。

画面の読み方、常時公開、スマホ/メールでの確認方法は
[docs/DASHBOARD_GUIDE.md](docs/DASHBOARD_GUIDE.md) にまとめています。

## GitHub Actions / Deployment

scheduled workflowは次の目標時刻です。

```text
08:20  morning_prediction -> 自動計算
08:45  morning_email      -> スマホへメール（08:50/08:55 retry）
09:00  利用者が自分で判断
15:45  close_update       -> 答え合わせ（15:55/16:10 retry）
```

GitHub Actionsは指定時刻ちょうどの開始を保証しません。実行が08:30を過ぎても、後から取得したsnapshotを朝の特徴量へ入れません。scheduled jobは`AUTOMATION_ENABLED=true`のrepository variableで安全に有効化する設計です。Neon/Streamlit/Gmail/GitHub secretsの手順は[Deployment](docs/DEPLOYMENT.md)を参照してください。

## 重要な計算

```text
target = raw_close / raw_open - 1
BUY = predicted_return > 0.003 AND probability_up >= 0.60
shares = floor(affordable shares / 100) × 100
net P/L = gross P/L - commission - slippage
```

defaultは1銘柄100万円、commission 5 bps/side、slippage 5 bps/side。これはbroker quoteではなくpaper simulation前提です。

## ドキュメント

- [確認・Dashboardガイド](docs/verification-and-dashboard-guide.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Data Sources](docs/DATA_SOURCES.md)
- [Data Dictionary](docs/DATA_DICTIONARY.md)
- [Modeling](docs/MODELING.md)
- [Backtest](docs/BACKTEST.md)
- [Metrics](docs/METRICS.md)
- [Operations](docs/OPERATIONS.md)
- [Deployment](docs/DEPLOYMENT.md)
- [全主要ファイル](docs/FILES.md)

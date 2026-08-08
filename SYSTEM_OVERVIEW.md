# システム全体説明書

更新日: 2026-08-09

日本株の「寄り付き→大引け」を予測するシステムの、全体像・実行内容・ファイル構成を
1つにまとめた説明書です。

**このシステムは研究用です。投資助言ではなく、利益を保証しません。自動発注も行いません。**

---

## 1. 何をするシステムか

毎営業日、次の流れを自動で回します。

```
08:20  海外市場のデータを取得 → 特徴量を作る → 22銘柄それぞれを学習 → 予測
08:30  ここが情報の締切（この時刻より後の情報は一切使わない）
08:45  予測結果をメールで送信
09:00  実際の寄り付き価格を観測
15:45  大引け後、実際の結果と答え合わせ → 損益・勝率を計算
```

売買ルールは **寄り付きで買って、同じ日の大引けで売る**。持ち越しはしません。

予測する値は `当日終値 ÷ 当日寄り付き − 1`（日中リターン）です。

### 買いの判断基準

> **予測リターン > 0.30%　かつ　上昇確率 >= 60%**

両方を同時に満たした銘柄だけを買います。片方だけでは買いません。0件の日も正常です。

---

## 2. 対象銘柄（22社）

| 業種 | 銘柄 |
|---|---|
| 海運 | 9101 日本郵船 / 9104 商船三井 / 9107 川崎汽船 |
| 石油・エネルギー | 1605 INPEX / 5020 ENEOS / 5019 出光興産 / 5021 コスモ |
| 自動車 | 7203 トヨタ / 7267 ホンダ / 7201 日産 / 7269 スズキ / 7270 SUBARU |
| 金融 | 8306 三菱UFJ / 8316 三井住友FG / 8411 みずほFG / 8604 野村 / 8766 東京海上 |
| 商社 | 8001 伊藤忠 / 8002 丸紅 / 8031 三井物産 / 8053 住友商事 / 8058 三菱商事 |

銘柄はコードに埋め込まれていません。`config/stocks.yaml` を編集すれば変更できます。

---

## 3. 最重要の設計思想: Look-Ahead Bias をゼロにする

**このシステムで一番大事なのは、予測精度ではなく「ズルをしていないこと」です。**

未来の情報を1つでも使うと、バックテストの成績は簡単に良くなります。しかし実運用では
再現しません。そのため、次の仕組みを何重にも入れています。

| 仕組み | 内容 |
|---|---|
| 締切の固定 | 対象日の 08:30 JST。実行が遅れても締切は動かしません |
| 3つの時刻を記録 | データが「公開された時刻」「初めて観測した時刻」「取得した時刻」を別々に保存 |
| 保存時の検査 | 3つとも締切以前でなければ、特徴量として保存を拒否します |
| 学習窓の分離 | 予測日ごとに直前120営業日だけで学習。標準化も欠損補完もその窓の中だけ |
| 時系列CV | `TimeSeriesSplit` のみ。ランダム分割は使いません |

### 実際にこの検査が働いた例

過去日（8/7）の予測を後から作ろうとしたとき、次のエラーで停止しました。

```
ValueError: raw input was not observed by the prediction cutoff
```

データを取得したのは8/9で、8/7の朝には手元に無かったためです。**これは不具合ではなく、
設計どおりの動作です。** 過去日を指定した手動実行は必ず失敗します。

過去期間の成績を見たい場合は、推定PITを使う専用の仕組み（`week-test` / `walk-forward`）
を使います。

---

## 4. データの入手元

| 種類 | 提供元 | 費用 | 注意 |
|---|---|---|---|
| 日本株・海外指標・FX・商品 | Yahoo Finance (yfinance) | 無料 | **非公式**。品質保証なし |
| 米国金利 (2Y/10Y/30Y) | U.S. Treasury 公式XML | 無料 | 値は公式。公表時刻は推定 |
| 補助・照合 | EODHD Free | 無料 | 任意。未設定でも動きます |

合計59系列（日本株22 + 海外指標37）を、1つずつ順番に取得します。並列で叩くとレート制限に
当たるため、意図的に逐次処理です。**これが実行時間の大半（10〜20分）を占めます。**

**Iron Ore は未解決**です。無料で08:30時点の再現性を確保できる提供元が見つからないため、
偽の代替値で埋めず、欠損のままにしてあります。

---

## 5. モデル

| 用途 | 使用モデル |
|---|---|
| 予測リターン | **Ridge回帰**（本番） |
| 上昇確率 | **ロジスティック回帰**（本番） |
| 比較用 | ElasticNet / Lasso / OLS（診断のみ。本番に自動昇格しません） |

- 学習窓: 各予測日の直前 **120営業日**
- 標準化・欠損補完: 学習窓の中だけで実施（漏洩防止）
- ハイパーパラメータ: 時系列CVで選択
- 乱数シード: 42 固定

比較モデルの成績を投資成績として読まないでください。同じ窓で順位付けした結果を成績として
報告すると、選択バイアスが入ります。

---

## 6. 評価指標

すべて **Out-of-Sample（予測時点で未知だったデータ）** のみで計算します。

| 指標 | 意味 |
|---|---|
| 勝率 | 勝ちトレード ÷ 全トレード |
| 金額ベース勝率 | 勝ち金額 ÷ 負け金額（Profit Factor） |
| Expectancy | 1トレードあたりの平均損益 |
| Sharpe / Sortino | リスク調整後リターン |
| Max Drawdown | 最大の落ち込み幅 |
| Readability | その銘柄をどれだけ安定して読めるか（0〜100） |
| Confidence | その日の予測の信頼度 |
| MAE / RMSE | 予測誤差 |

**20トレード未満は `LOW_SAMPLE` として警告します。** 少数の勝率は偶然で大きく動くため、
判断材料になりません。

---

## 7. 稼働環境

| 役割 | 使用サービス | 費用 |
|---|---|---|
| 定期実行 | GitHub Actions | 無料 |
| データベース | Neon (PostgreSQL) | 無料 |
| ダッシュボード | Streamlit Community Cloud | 無料 |
| メール送信 | Gmail SMTP | 無料 |

公開URL: https://japan-stock-predictor-ky1.streamlit.app
ソース: https://github.com/ken-yokota/japan-stock-predictor

ローカルのMacにもPostgreSQLとダッシュボードがありますが、こちらは検証用です。
本番データはNeonにあります。

---

## 8. ダッシュボードの画面

入口は `app.py` だけです。残りは `pages/` から自動で読み込まれます。

| 画面 | ファイル | 内容 |
|---|---|---|
| Overview | `app.py` | DB接続状態、最新の実行状況、各ページへの入口 |
| Today | `pages/1_Today.py` | 当日のBUY候補、予測、実績Open、品質警告 |
| Stock Detail | `pages/2_Stock_Detail.py` | 銘柄別の予測・実績履歴、累積損益 |
| Factor Analysis | `pages/3_Factor_Analysis.py` | BUY条件の表示、指標の係数と安定性 |
| Sector Analysis | `pages/4_Sector_Analysis.py` | 業種別の横断比較 |
| Backtest | `pages/5_Backtest.py` | 保存済みOOS成績＋条件を変えた再計算 |
| System Status | `pages/6_System_Status.py` | 実行履歴、Provider状況、データ鮮度 |
| **テスト** | `pages/7_Test.py` | 検証期間の日別勝率、買った銘柄、係数推移 |
| **Company Analysis** | `pages/8_Company_Analysis.py` | 企業別の予測推移と、どの指標がどの係数で効いたか |

ダッシュボードは **DBのSELECTしか行いません**。画面を開いてもデータ取得・学習・メール送信は
起動しません。この制約はテストで機械的に保証しています。

---

## 9. フォルダ構成

```
japan-stock-predictor/
├── app.py                  ダッシュボード入口
├── cli.py                  全コマンドの単一入口
├── pages/                  ダッシュボードの各画面（1〜8）
│
├── config/                 設定（コードを触らず変更できる部分）
│   ├── stocks.yaml           対象22銘柄とProvider別シンボル
│   ├── indicators.yaml       海外37指標、品質、業種対応
│   ├── model.yaml            学習窓120日、特徴量、CV、候補モデル
│   ├── trading.yaml          BUY閾値、投資額、手数料、スリッページ
│   └── settings.yaml         Provider設定、スケジュール、品質基準
│
├── data/                   データ取得と時点管理
│   ├── providers/            Yahoo / Treasury / EODHD の実装
│   ├── provider_router.py    品質・鮮度で単一Providerを選択
│   ├── availability.py       08:30締切の導出
│   ├── alignment.py          as-of選択とLook-Ahead拒否
│   └── market_calendar.py    JPX営業日判定（祝日対応）
│
├── database/               DBスキーマと保存
│   ├── models.py             全テーブル定義
│   ├── repository.py         冪等な保存とPIT検査
│   └── connection.py         接続（search_path固定を含む）
├── alembic/                DBマイグレーション（0001〜0003）
│
├── features/               特徴量計算
├── models/                 Ridge / Logistic / 比較モデル
├── backtest/               walk-forward と条件変更シナリオ
├── trading/                BUY判定、株数計算、損益
├── metrics/                勝率・PF・Sharpe などの計算
├── scoring/                Readability / Confidence / 係数安定性
│
├── services/               DBを跨ぐ処理の組み立て
├── pipeline/               朝 / 寄り付き / 大引け の3つの流れ
├── notifications/          メール本文の生成と送信
│
├── scripts/                実行スクリプト
│   ├── run_morning_prediction.py   08:20の処理
│   ├── send_morning_email.py       08:45のメール
│   ├── update_open.py              寄り付き値の観測
│   ├── run_close_update.py         15:45の答え合わせ
│   ├── run_week_test.py            DBなしの検証（研究用）
│   ├── run_buy_all_reference.py    全部買った場合の対照
│   ├── bootstrap_history.py        履歴の初期取得
│   ├── start_dashboard.sh          ダッシュボード起動
│   └── send_test_result_email.sh   検証結果のメール送信
│
├── tests/                  pytest (176件)
├── docs/                   詳細ドキュメント
└── .github/workflows/      定期実行の定義
```

---

## 10. コマンド一覧

```bash
cd ~/Desktop/japan-stock-predictor

python -m cli                    # コマンド一覧
python -m cli config-check       # 設定の検証（秘密情報不要）
python -m cli morning            # 朝の予測（当日）
python -m cli send-email --dry-run   # メール本文の確認（送信しない）
python -m cli close              # 大引け後の答え合わせ
python -m cli week-test --from-date 2026-07-01 --to-date 2026-08-07
python -m cli buy-all --from-date 2026-07-01 --to-date 2026-08-07
python -m cli dashboard          # ダッシュボード起動
```

`week-test` は「過去の教師データで学習し、指定期間を検証する」研究用の仕組みです。
`buy-all` は「全銘柄を無条件に毎日買った場合」の対照結果で、モデルの絞り込みに
価値があったかを比べる基準になります。

---

## 11. 現時点の検証結果（2026-07-01 〜 08-07）

| | BUY判定あり | 全銘柄を毎日購入（対照） |
|---|---|---|
| 取引数 | 13 | 594 |
| 勝率 | 76.9% | 52.4% |
| 金額ベース勝率 | 3.085 | 1.120 |
| 純損益 | +39,146円 | +330,598円 |
| 1取引あたり | +3,011円 | +557円 |

**この数字を有効性の証拠として扱わないでください。** 13トレードでは統計的に何も言えません。
利益の6割近くが川崎汽船1件（+23,030円）に集中しており、これを除くと+16,116円まで下がります。

判断できるようになるのは、BUYシグナルが20件以上貯まってからです。

---

## 12. 既知の限界

- Yahoo/yfinanceは**非公式**。symbol・仕様・提供継続が予告なく変わり得ます
- 過去データには「当時何時に入手できたか」の記録が無いため、バックテストは実運用より
  **良く見える可能性**があります
- GitHub Actions の開始時刻は保証されません（数分〜十数分ずれます）
- Gmail SMTP には重複送信を完全に防ぐ仕組みがありません。DB側で抑止していますが、
  「送信直後に通信断」のような曖昧な失敗では厳密な保証ができません
- 手数料・スリッページは各片側5bpsの仮定値であり、実際の約定を再現しません
- 板・流動性・税金・呼値は再現していません
- Backtest画面で条件を何通りも試して最良を選ぶと、選択バイアスが入ります

---

## 13. 変更したいときにどこを見るか

| やりたいこと | 編集する場所 |
|---|---|
| 銘柄を増やす・減らす | `config/stocks.yaml` |
| 指標を追加する | `config/indicators.yaml` |
| BUY条件を変える | `config/trading.yaml` |
| 投資額・手数料を変える | `config/trading.yaml` |
| 学習期間を変える | `config/model.yaml` |
| メール文面を変える | `notifications/templates.py` |
| 実行時刻を変える | `.github/workflows/` |
| 画面表示を変える | 該当する `pages/` |

---

## 14. 関連ドキュメント

| 知りたいこと | ファイル |
|---|---|
| いま何をすればいいか | `START_HERE.md` |
| 外部サービスの設定手順 | `docs/SETUP_CHECKLIST.md` |
| 画面の見方 | `docs/DASHBOARD_GUIDE.md` |
| 全ファイルの詳細 | `docs/FILES.md` |
| 指標の数式 | `docs/METRICS.md` |
| 無料データの限界 | `docs/KNOWN_ISSUES.md` |
| 設計上の前提 | `docs/ASSUMPTIONS.md` |
| このMacへの変更と戻し方 | `docs/CHANGE_LOG_LOCAL.md` |

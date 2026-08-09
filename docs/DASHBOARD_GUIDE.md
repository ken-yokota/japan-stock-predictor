# ダッシュボード案内

更新日: 2026-08-08

このファイルだけを読めば「どのファイルを開けばダッシュボードが出るか」「各画面に何が
書いてあるか」「常に見られるようにするには何が必要か」が分かるようにしています。

本アプリは研究用です。投資助言、利益保証、自動発注は行いません。

---

## 1. 起動するファイルは `app.py` だけ

ダッシュボードの入口は **`app.py`** です。これ1つを Streamlit に渡せば、残りの6画面は
`pages/` から自動で読み込まれます。個別のページファイルを直接起動する必要はありません。

```bash
cd /Users/yokotaken/Desktop/japan-stock-predictor
./scripts/start_dashboard.sh
```

このスクリプトは venv を有効化し、`.env` を読み、`streamlit run app.py` を実行します。
起動すると <http://localhost:8501> が開きます。

`DATABASE_URL` が未設定でも画面は開きます。その場合は各画面が「PENDING: DATABASE_URLが
未設定」と表示します。これは異常ではなく、まだ見せられる保存データが無いという表示です。

---

## 2. 画面とファイルの対応

| 開く画面 | ファイル | 何が書いてあるか |
|---|---|---|
| Overview（最初の画面） | [app.py](../app.py) | DB接続状態、最新pipelineの状態、最新予測の状態、各ページへのリンク |
| Today | [pages/1_Today.py](../pages/1_Today.py) | 当日のBUY候補、予測リターン、上昇確率、実績Open、Open基準の予測終値、品質警告 |
| Stock Detail | [pages/2_Stock_Detail.py](../pages/2_Stock_Detail.py) | 銘柄1つの予測履歴と実績の突き合わせ、累積損益、係数履歴 |
| Factor Analysis | [pages/3_Factor_Analysis.py](../pages/3_Factor_Analysis.py) | どの海外指標がどれだけ効いたか（標準化係数） |
| Sector Analysis | [pages/4_Sector_Analysis.py](../pages/4_Sector_Analysis.py) | 海運・エネルギー・自動車・金融・商社の業種別比較 |
| Backtest | [pages/5_Backtest.py](../pages/5_Backtest.py) | 保存済みOOS成績と、**閾値・投資額・コストを変えた再計算** |
| System Status | [pages/6_System_Status.py](../pages/6_System_Status.py) | run履歴、Provider採用状況、鮮度、DB状態 |
| テスト | [pages/7_Test.py](../pages/7_Test.py) | 直近期間の検証結果。日別勝率、金額ベース勝率、寄り付き/大引けの予測と実績、銘柄別の係数推移 |
| Company Analysis | [pages/8_Company_Analysis.py](../pages/8_Company_Analysis.py) | 企業別の予測値の推移と、どの指標がどの係数で効いたか。新しく使われ始めた指標も表示 |

画面の中身を組み立てている共通部品は次の4つです。表示を直したいときはページ本体ではなく
こちらを見ます。

| ファイル | 役割 |
|---|---|
| [dashboard/ui.py](../dashboard/ui.py) | ページ共通の枠、サイドバー、キャッシュ（60秒）、DB接続の入口 |
| [dashboard/query_service.py](../dashboard/query_service.py) | DBへのSELECT文。ここにしかSQLはありません |
| [dashboard/presenters.py](../dashboard/presenters.py) | 数値の書式、警告の組み立て、表の行生成 |
| [dashboard/catalog.py](../dashboard/catalog.py) | 銘柄コード → 会社名・業種の表示名 |

ダッシュボードは **DBのSELECTしか行いません**。画面を開いてもデータ取得、モデル学習、
メール送信は起動しません。

---

## 3. 各画面の読み方

### Today — 最初に見る画面

読む順番を固定してください。

1. **画面上部の警告**（赤・黄）。ここが出ている日は予測値より先に原因を確認します。
2. **Data Cutoff**。特徴量に入れてよい情報の上限時刻（08:30 JST）です。
3. **BUY候補の件数**。0件も正常な結果です。「条件を満たす銘柄が無かった」という意味です。
4. **全銘柄の表**。判定、予測リターン、上昇確率、Confidence、Feature Coverage を見ます。

`実績Open` と `予測終値(Open基準)` は 09:00 以降に寄り付き値を取得できてから入ります。
それまでは `PENDING` です。朝の時点では当日Openが存在しないため、前日終値ベースの数字を
Open基準の予測終値として見せることはしません。

全銘柄が `INSUFFICIENT_DATA` の日は、予測モデルの問題ではなく履歴データかProviderの問題です。

### Backtest — 2つのタブがあります

- **保存済みOOS結果**: close pipelineがDBへ保存した確定値です。画面を開いても再計算しません。
- **条件を変えて再計算**: 保存済みのwalk-forward予測に対して、予測リターン閾値・上昇確率の
  下限・1日のTop N・投資額・手数料・スリッページを変えて集計し直します。

再計算タブで変えられないものが2つあります。

- **モデル（Ridge / ElasticNet など）** と **学習期間（120営業日）**。これらは「予測そのもの」
  が変わるため、画面上の再集計では扱えません。`python -m cli walk-forward` を実行して
  予測を作り直してください。

再計算タブは何通りでも試せますが、**試した回数を画面が数えて警告します**。良い数字が出た条件
だけを採用すると selection bias が入り、実際の成績はその数字より悪くなります。

### テスト

`python -m cli week-test` が書き出したJSONを読むだけのページです。画面を開いても再計算
しません。`artifacts/week_test/` に置いたJSONが1つずつタブになり、検証期間の開始日が
早い順に並びます。各タブの中に、日別の勝率、金額ベースの勝率、寄り付き・大引けの予測と
実績、各指標の係数と前日差が入っています。

タブが分かれているのは、期間の長さで数字の信頼度が変わるからです。期間を延ばすと予測
件数が増えるので**方向的中率**は読めるようになりますが、BUY件数はさほど増えないため
**勝率と損益はどのタブでも証拠になりません**。

見るときの順番は、まず**BUYシグナルの件数**です。20件未満なら赤い警告が出ます。件数が
少ない勝率は偶然で大きく動くので、パーセンテージより先に母数を見てください。

「予測終値」が2列あるのは、朝の時点では当日の寄り付きが存在しないためです。朝は前日終値
を基準に出し、寄り付きが判明してから実際の寄り付きを基準に引き直します。混ぜて1列にすると
「寄り付きを知った上で予測した」ように見えてしまうため、分けています。

**「終値-寄付」列**は、同じ予測を率ではなく円で表したものです。`終値 − 寄り付き` で、
予測と実績を隣り合わせに置いてあります。率は銘柄をまたいで比較でき、円は実際にいくら
動いたかが分かります。株価水準が違う銘柄では、同じ0.5%でも金額が大きく変わります。

この検証はDBパイプラインの外で動くので、Provider品質ゲートとPIT lineageを通っていません。
実運用より良く見える可能性があります。

比較の基準として `python -m cli buy-all` があります。BUY判定を無視して全銘柄を毎日買った
場合の結果です。モデルの絞り込みがこれを上回っていなければ、絞り込みは価値を生んでいません。

### Company Analysis

企業を1社選ぶと、その企業の予測がどう動いたかと、**どの指標がどの係数で効いていたか**を
営業日ごとに追えます。

「新しく現れた指標」は、設定ファイルの指標リストではなく**実際の係数**で判定しています。
Ridgeは効かない指標の係数をちょうど0に潰すため、0を抜けた日がモデルがその指標を使い
始めた日になります。毎回リストにあっても係数が0のままの指標は「未使用」と表示します。

係数は標準化後の値です。同じ銘柄・同じモデルの中でのみ大小を比較できます。
**係数が大きいことは「その指標を見れば儲かる」という意味ではありません。**

Factor Analysis にも同じ推移表があります。違いは読んでいるデータで、Company Analysis は
検証結果ファイル、Factor Analysis は**本番DBに保存された学習結果**を見ています。

### System Status

DBに最後に保存された監査情報です。Providerへの live ping ではありません。
`FALLBACK`、`STALE`、`MISSING`、`FREE_UNVERIFIED` が増えた日は、予測値よりデータ品質を
先に確認してください。

---

## 4. 常に確認できるようにする

「常に」の意味が2つあるので分けて書きます。

### A. スマホからいつでも見たい → Streamlit Community Cloud（無料）

これが本来の常時公開の形です。Mac の電源が切れていても見られます。
手順とURLは [docs/SETUP_CHECKLIST.md](SETUP_CHECKLIST.md) にまとめてあります。要点だけ挙げると:

1. Neon（無料PostgreSQL）でDBを作り、接続URLを取得する。
2. GitHub リポジトリを Streamlit Community Cloud へ接続する。
3. entry point を `app.py`、Python を 3.12 に設定する。
4. Streamlit Secrets へ `DATABASE_URL` だけを登録する（Provider keyやSMTP情報は不要）。
5. 発行されたURLをスマホのホーム画面に追加する。

**この5つはアカウント作成と秘密情報の登録を含むため、利用者本人しか実行できません。**
コード側の準備は完了しています。

データ利用許諾の観点から、公開URLは本人限定にしてください。Yahoo/yfinance のデータを
第三者へ再配布できるかは未確認です（[docs/KNOWN_ISSUES.md](KNOWN_ISSUES.md)）。

### B. このMacで常に立ち上がっていてほしい → LaunchAgent

Mac にログインしたら自動でダッシュボードが起動する設定です。Mac が起動している間だけ
有効で、同じWi-Fi以外からは見られません。

```bash
cp scripts/com.jpstock.dashboard.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jpstock.dashboard.plist
```

停止するとき:

```bash
launchctl unload ~/Library/LaunchAgents/com.jpstock.dashboard.plist
```

ログは `~/Library/Logs/jpstock-dashboard.log` に出ます。

### C. メールで受け取りたい

ダッシュボードを開かなくても、毎営業日 08:45 頃に上位候補をメールで受け取れます。
実装は完了しており、Gmail の App Password 登録だけが残っています。

```bash
# 外部送信せず、本文だけ確認する
python -m cli send-email --prediction-date 2026-08-10 --dry-run
```

メールに入るのは日付、上位5銘柄、予測リターン、上昇確率、Readability、Profit Factor、
勝率、Expectancy、Confidence、Positive/Negative Factors、データ品質、ダッシュボードURLです。
BUY候補が0件の日は「本日は条件を満たすBUY候補なし」と明記されます。

---

## 5. 画面に数字が入らないとき

ダッシュボードは自分でデータを作りません。次の順番で用意します。

```bash
# 1. 設定が壊れていないか（秘密情報なしで実行できます）
python -m cli config-check

# 2. 履歴データを入れる
python -m cli bootstrap-history --from-date 2023-08-01 --to-date 2026-08-07

# 3. 朝の予測を作る（実在するJPX営業日を指定）
python -m cli morning --prediction-date 2026-08-10

# 4. 大引け後に答え合わせをする
python -m cli close --prediction-date 2026-08-10
```

`3` を実行するまで Today は空、`4` を実行するまで Backtest の成績は空です。これは異常では
ありません。

---

## 6. 関連ドキュメント

| 知りたいこと | ファイル |
|---|---|
| 外部サービス登録の手順とURL | [docs/SETUP_CHECKLIST.md](SETUP_CHECKLIST.md) |
| 全ファイルの役割 | [docs/FILES.md](FILES.md) |
| 今どこまで実装できているか | [docs/IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) |
| 数字の検証方法とSQL | [docs/verification-and-dashboard-guide.md](verification-and-dashboard-guide.md) |
| 指標の数式 | [docs/METRICS.md](METRICS.md) |
| 無料データの限界 | [docs/KNOWN_ISSUES.md](KNOWN_ISSUES.md) |
| デプロイ手順 | [docs/DEPLOYMENT.md](DEPLOYMENT.md) |

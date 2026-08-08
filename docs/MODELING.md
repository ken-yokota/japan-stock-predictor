# モデリング

更新日: 2026-08-08

## Targetと予測単位

モデルは銘柄別に独立して学習する。予測対象は同一JPX営業日の寄り付きから大引けまでのraw価格リターンである。

```text
intraday_return = raw_close / raw_open - 1
price_difference = raw_close - raw_open
```

Adjusted Closeはtargetに使わない。朝のreference priceは前日終値で、表示用`predicted_close = reference × (1 + predicted_return)`を作る。これは当日Openを予測したものではない。

## Training windowとPIT

- 予測日より前のJPX 120営業日を固定windowとする。
- `training_end < prediction_date`をDB constraintとtestで検査する。
- score行のcutoffは08:30 JST。運用scoreではavailable、first observed、retrievedの全時刻がcutoff以前である必要がある。
- target株の当日OHLCはscore特徴量に使わない。target-stock technicalは直前sessionまで。
- historical rawに正確な初回観測時刻がない場合、market close + lagに基づくestimated PITでtraining rowを構成する。これは完全なhistorical tick replayではない。
- featureからraw row IDとraw hashまで`feature_inputs`へlineageを保存する。

20-session feature warmupがあるため、bootstrapでは120日より余裕を持つ履歴が必要である。朝pipelineは既定で過去550暦日を取得対象にする。

## Feature selectionと欠損

共通、sector、ticker固有の指標IDは`config/indicators.yaml`から解決する。価格系列ごとに1/2/3/5/20日return、log return、5/20日volatility、前日intraday range、high-low range、MA20乖離等を作る。Treasuryはlevel、10Y−2Y、1/3/5観測日変化を含む。

当日利用できない、stale、品質不足、未解決の特徴量はscore行から除外する。未来の値や後日取得値で補完しない。training featureの欠損率が20%を超える場合、または完全なtargetが120行未満の場合はfail closedで`INSUFFICIENT_DATA`とする。

残った欠損はscikit-learn Pipeline内のmedian imputerで扱う。imputerとStandardScalerは各CV training foldだけでfitされ、full datasetで先にfitしない。

## モデル

| Task | Primary | 目的 |
|---|---|---|
| Regression | Ridge | `intraday_return`の連続値 |
| Classification | Logistic Regression | `intraday_return > 0`の確率 |

候補gridは`config/model.yaml`で管理する。Ridge alphaは`0.01, 0.1, 1, 10, 100`、Logistic Cは`0.01, 0.1, 1, 10`。5分割`TimeSeriesSplit`で過去から未来の順に評価し、Ridgeはvalidation MSE、Logisticはvalidation log lossで選ぶ。最終モデルは選択値で120-session training windowに再fitする。random seedは42。

direction targetがtraining内で単一classならLogisticをfitせず、training内の上昇比率を定数確率として使う。これもstatus・parameterとともに保存する。

設定にはElasticNet、OLS、Lasso候補名があるが、現在のproduction training serviceが実行するのはRidgeとLogisticだけである。候補名があることを実装済みモデルと解釈しない。

## 予測、区間、説明

- 回帰値: fitted Ridgeの1-row出力。
- 上昇確率: Logistic `predict_proba`または単一class定数。
- 簡易95%幅: `prediction ± 1.96 × in-window training residual sample SD`。
- positive/negative factors: 標準化Ridge係数の正/負上位3feature名。
- confidence: magnitude、確率conviction、過去Readability、feature coverageの表示用合成score。

簡易95%幅はheteroskedasticity、parameter uncertainty、時系列依存を十分にモデル化した統計的prediction intervalではない。係数は同一model・同一scaling内の感応度であり、因果効果を示さない。

## BUY rule

```text
BUY = predicted_return > 0.003 AND probability_up >= 0.60
```

それ以外は`NO_BUY`。欠損やnon-finite出力はBUYにしない。BUY順位は予測return降順、次に確率降順、最後にtickerで決める。

## 再現性

feature/model/strategyにはversion label、全validated configにはSHA-256、raw lineage manifestにもSHA-256を保存する。モデル係数、intercept、hyperparameter、scaler mean/scaleをDBに保存する。ただし外部Providerが過去データを訂正し、当時のrevisionを初回取得していなかった場合、後日の完全再現には限界がある。

# Phase 0: 無料データ実現性

更新日: 2026-08-08

## 結論

Yahoo Finance + U.S. Treasuryだけで、価格取得、PIT gate、特徴量、モデル、朝予測までのsystem pathを実装できる。EODHD Free keyはなくても動作し、設定した場合だけquota制限付きEOD fallback/比較に使う。ただし「無料で取得可能」と「08:30に安定して利用可能」「再配布可能」は同義ではない。

初期tierは次のとおり。

| Tier | 判定 | 対象 |
|---|---|---|
| A | 無料経路があり、通常は前日EODとして利用可能 | 日本株22、主要米国指数/ETF/ADR、Treasury |
| B | 無料経路はあるが、08:30 snapshotの鮮度・delayを毎回検査 | FX、Nikkei/S&P/NASDAQ futures、商品先物、Dollar Index |
| C | proxyであり、直接instrumentと同義ではない | BDRY、EODHDのSPY/QQQ/DIA等proxy |
| D | 無料で許諾・PITを確認できるsource未解決 | Iron Ore、Baltic Capesize/Panamax、US shipping equity proxy |

Tier B/Dは取得失敗やstaleならその日のfeatureから除外する。未来情報で補完しない。

## 実行方法

設定だけの検査:

```bash
python -m scripts.phase0_data_feasibility
python -m data.fetch config-check
```

Yahooへbest-effort接続する検査:

```bash
python -m scripts.phase0_data_feasibility --network
python -m data.fetch verify-yahoo
```

EODHD keyを設定した場合のみ:

```bash
python -m data.fetch verify-eodhd
python -m data.fetch compare-eod \
  --from-date 2026-08-01 --to-date 2026-08-07 --max-series 5
```

## 合格基準

- configがstrict validationを通り、target 22銘柄が明示的symbolを持つ。
- 同一seriesを複数Providerの行で穴埋め混在しない。
- operational snapshotはcutoff、future timestamp、max age、quality gateを通る。
- raw値と全timestamp、provider、quality、raw hashを保存できる。
- 取得失敗が予測job全体のfuture fillにつながらず、feature除外または`INSUFFICIENT_DATA`になる。
- 最低2〜3年の履歴をbootstrapし、120-session OOS windowを複数回作れる。

## 現時点の確認状態

Provider/config/PIT/router/DBのunit test、Yahoo/Treasury/EODHDのmock testは存在する。ネットワークの実データは外部状態に依存し、CIでは固定fixture/mockを使う。2026-08-08時点の実装監査では朝pipelineのend-to-end codeはあるが、実Neon DBと実08:20 jobを連続営業日で運用したdata quality reportはまだない。無料版の有効性は、今後のbootstrapとwalk-forward OOS永続化、日次観測で確認する必要がある。

より詳しい全series表は`docs/data-source-coverage.md`を参照する。

# Arli系モデル向け 作業指示書

対象リポジトリ: `like-kradness-2025/stablecoin-monitor`

対象ブランチ: `feat/v3-refactor-responsibilities`

想定モデル: Arli系の比較的小さめモデル。例: `Qwen3.5-27B`, `GLM-4.7`, `Gemma-4-31B` など。

目的: `stablecoin-monitor v3.00` のCCXT OHLCV版を、**段階的・計画的・サブエージェント分業**で安全に改善する。

この指示書は、単独モデルが勢いで全部直すためのものではない。必ず小さな工程に分け、各工程をサブエージェントに割り当て、レビューゲートを通してから次へ進む。

---

## 0. 最重要ルール

あなたはこの作業を、**保守担当エンジニア兼作業管理者**として行う。

絶対に守ること。

1. 一度に大改造しない。
2. 作業は必ずPhaseに分ける。
3. 実務作業はサブエージェントに分担させる。
4. 各サブエージェントには狭い責務と明確な成果物だけを渡す。
5. サブエージェントの出力をそのまま信用しない。
6. `python stablecoin_monitor.py --once` と `--loop` の入口を維持する。
7. APIキー不要のCCXT public OHLCV方針を維持する。
8. Proxy CVDをTrue CVDと誤表現しない。
9. `market_key = exchange:symbol` を監視単位にする。
10. 取引所ごとの失敗で全体停止させない。
11. DB破壊的変更をしない。
12. READMEと`.env.example`の整合性を壊さない。
13. pytestでは外部ネットワークを叩かない。
14. レビューで95点未満なら、必ず改善して再レビューする。

---

## 1. 現在の設計前提

v3.00は以下の流れで動く。

```text
CCXT public OHLCV
↓
取引所単位ペアの1m足取得
↓
OHLCVからProxy Delta算出
↓
Proxy CVDをSQLiteへ保存
↓
5分ごとにチャート生成
↓
Discord通知または標準出力
```

これは **True CVDではない**。

```text
Proxy CVD = OHLCVから推定した売買圧
True CVD  = 約定ごとのtaker buy / taker sell累積
```

この区別を崩してはいけない。

---

## 2. 現在のモジュール構成

```text
stablecoin_monitor.py        # CLI入口、argparse、loop制御のみ
stablecoin_monitor/
  app.py                     # fetch/store/render/notifyのオーケストレーション
  ccxt_fetcher.py            # CCXT OHLCV取得
  charting.py                # PNG描画
  config.py                  # .env / Settings読み込み
  constants.py               # 定数
  db.py                      # SQLite接続、schema、保存、読込
  exceptions.py              # ConfigError
  flow.py                    # フロー分類
  formatting.py              # 表示整形
  market_registry.py         # markets.yaml読込・検証
  models.py                  # dataclass群
  notifier.py                # Discord送信
  proxy_cvd.py               # OHLCV → proxy_delta計算
```

責務境界を守ること。

| モジュール | やってよいこと | やってはいけないこと |
|---|---|---|
| `stablecoin_monitor.py` | CLI、logging、bootstrap、loop | DB処理、CCXT処理、CVD計算、描画 |
| `app.py` | 各部品の接続、エラー隔離 | 低レベルSQL、CCXT生呼び出し、描画ロジック |
| `ccxt_fetcher.py` | CCXT exchange作成、load_markets、fetch_ohlcv | DB保存、分類、描画 |
| `proxy_cvd.py` | OHLCVからdelta算出、row parse | CCXT呼び出し、DB保存 |
| `db.py` | SQLite schema、保存、読込、prune | CCXT取得、分類、描画 |
| `flow.py` | delta集計、signal分類 | DB接続、CCXT取得、描画 |
| `charting.py` | matplotlib描画 | データ取得、DB保存 |
| `market_registry.py` | YAML読込、market validation | CCXT存在確認、DB保存 |
| `config.py` | env読み込み、Settings生成 | market YAML読み込み、DB初期化 |
| `notifier.py` | Discord送信 | summary生成、chart生成 |

---

## 3. サブエージェント分業ルール

実務は必ずサブエージェントに分担させる。

| Subagent | 役割 | 触ってよい範囲 | 触ってはいけない範囲 |
|---|---|---|---|
| `Planner` | 作業分解、順序設計 | docs, task list | 実装変更 |
| `TestWriter` | 単体テスト追加 | tests/, requirements.txt | 本体大改造 |
| `CodeFixer` | 小規模実装修正 | 指定本体1〜2ファイル | 関係ない全体整理 |
| `Reviewer` | 敵対的レビュー | 差分、テスト結果 | 実装変更 |
| `DocsMaintainer` | README/docs整合性 | README.md, docs/, .env.example | 本体ロジック |
| `IntegrationChecker` | compileall, pytest, smoke確認 | 実行確認・ログ整理 | 大規模修正 |

サブエージェントには必ず以下を渡す。

```text
- 目的
- 対象ファイル
- 禁止事項
- 成果物
- 実行すべき確認
- 報告フォーマット
```

サブエージェントの出力は、そのまま採用しない。親エージェントが必ず以下を確認する。

```text
- 指示範囲を超えていないか
- Proxy CVDをTrue CVDと誤表現していないか
- market_keyが崩れていないか
- APIキー必須に戻していないか
- DB破壊的変更をしていないか
- テストが外部ネットワークに依存していないか
```

---

## 4. Arli系モデル運用ルール

Arli系モデルは、長い作業で指示逸脱・過剰修正・自己採点の甘さが出やすい前提で運用する。

1回の依頼は短く、狭く、検証可能にする。

悪い依頼:

```text
全体をいい感じに改善して
```

良い依頼:

```text
tests/test_proxy_cvd.py だけ追加してください。
本体コードは変更しないでください。
以下の9ケースをpytestで検証してください。
完了後、変更ファイルと実行結果だけ報告してください。
```

禁止事項:

```text
- 自己判断でアーキテクチャを作り替えない
- 自己判断でWebSocketやTrue CVDを追加しない
- 自己判断でAPIキー必須設計に戻さない
- 自己判断でDBスキーマを破壊しない
- 自己判断でREADMEの仕様を変えない
- 自己判断でペア一覧を大幅変更しない
```

サブエージェントにはリポ全体を読ませない。必要最小限だけ渡す。

---

## 5. 推奨Phase計画

### Phase 0: 事前確認

担当: `Planner` + `IntegrationChecker`

```bash
git status
git branch --show-current
python -m compileall stablecoin_monitor stablecoin_monitor.py
```

完了条件:

```text
- ブランチが feat/v3-refactor-responsibilities
- compileallが通る
- 失敗があれば内容を記録する
```

---

### Phase 1: proxy_cvd単体テスト追加

担当: `TestWriter`

対象:

```text
stablecoin_monitor/proxy_cvd.py
tests/test_proxy_cvd.py
```

本体変更: 原則なし。

必須テスト:

```text
1. close > open かつ closeが高値寄り → proxy_delta > 0
2. close < open かつ closeが安値寄り → proxy_delta < 0
3. high == low かつ close > open → proxy_delta = +volume
4. high == low かつ close < open → proxy_delta = -volume
5. volume == 0 → proxy_delta = 0
6. proxy_deltaが [-volume, +volume] を超えない
7. timeframe_to_seconds('1m') == 60
8. timeframe_to_seconds('5m') == 300
9. 不正timeframeでConfigError
```

確認:

```bash
pytest tests/test_proxy_cvd.py
```

---

### Phase 2: market_registry単体テスト追加

担当: `TestWriter`

対象:

```text
stablecoin_monitor/market_registry.py
tests/test_market_registry.py
```

必須テスト:

```text
1. 正常なmarkets.yamlを読める
2. market_key重複でConfigError
3. market_keyとexchange:symbol不一致でConfigError
4. base == quote でConfigError
5. 不正categoryでConfigError
6. enabled=false市場はenabled_marketsから除外される
```

注意:

```text
tmp_pathで一時YAMLを作る。
実ファイル config/markets.yaml を直接書き換えない。
```

確認:

```bash
pytest tests/test_market_registry.py
```

---

### Phase 3: flow.pyの時刻注入対応

担当: `CodeFixer` + `TestWriter`

目的: フロー分類テストを安定させる。

対象:

```text
stablecoin_monitor/flow.py
tests/test_flow.py
```

必須変更:

```python
def category_recent_delta(..., now_utc: datetime | None = None):
    now_utc = now_utc or datetime.now(UTC)
```

`classify_flow()` も `now_utc` を受け取れるようにする。

必須テスト:

```text
1. btc_stable deltaが閾値超え、fiat_delta >= 0 → BTC_DEMAND
2. stable_fiat deltaが閾値超え、btc_delta弱い → STABLE_INFLOW
3. stable_cross deltaが閾値超え、btc_delta弱い → STABLE_ROTATION
4. btc_stable deltaが負方向閾値超え → BTC_RISK_OFF
5. btc_stable負、stable_fiat負 → FIAT_EXIT
6. どれでもない → MIXED
```

確認:

```bash
pytest tests/test_flow.py
```

---

### Phase 4: config単体テスト追加

担当: `TestWriter`

対象:

```text
stablecoin_monitor/config.py
tests/test_config.py
```

必須テスト:

```text
1. .envなしでもデフォルトSettingsが作れる
2. OHLCV_LIMIT < 2 でConfigError
3. 相対パスがbase_dir基準でresolveされる
```

注意:

```text
os.environを汚染しない。
monkeypatchを使う。
```

確認:

```bash
pytest tests/test_config.py
```

---

### Phase 5: DB単体テスト追加

担当: `TestWriter`

対象:

```text
stablecoin_monitor/db.py
tests/test_db.py
```

必須テスト:

```text
1. init_dbでv2互換snapshotsとv3テーブルが作られる
2. save_ohlcv_barsで1件保存できる
3. 同じprimary keyでreplaceされる
4. recompute_proxy_cvdで累積値が正しくなる
5. prune_dbで古いohlcv_barsを削除できる
```

注意:

```text
tmp_path上のSQLiteを使う。
既存DBを触らない。
```

確認:

```bash
pytest tests/test_db.py
```

---

### Phase 6: CCXT fetcherのmockテスト追加

担当: `TestWriter`

対象:

```text
stablecoin_monitor/ccxt_fetcher.py
tests/test_ccxt_fetcher.py
```

必須方針:

```text
単体テストで外部ネットワークを叩かない。
CCXT exchangeはfake/mockにする。
```

必須テスト:

```text
1. fetchOHLCV非対応ならskipされる
2. symbol未対応ならskipされる
3. 1市場の例外で全体fetchが止まらない
4. KeyboardInterruptは握りつぶさない
5. timeframe fallbackが効く
```

確認:

```bash
pytest tests/test_ccxt_fetcher.py
```

---

### Phase 7: README/docs整合性更新

担当: `DocsMaintainer`

対象:

```text
README.md
.env.example
docs/QWEN35_27B_WORK_INSTRUCTION.md
```

必須内容:

```text
- stablecoin_monitor.py はCLI入口のみ
- 実処理は stablecoin_monitor/ 配下へ分割済み
- Proxy CVDでありTrue CVDではない
- APIキー不要のCCXT public OHLCV
- テスト実行方法
```

---

### Phase 8: 統合確認

担当: `IntegrationChecker`

必須確認:

```bash
python -m compileall stablecoin_monitor stablecoin_monitor.py
pytest
```

可能なら:

```bash
python stablecoin_monitor.py --once
```

`--once`をSKIPできる条件:

```text
- ネットワーク不可
- 依存未導入
- CCXT接続不可
- 実行環境にmatplotlib backend問題がある
```

SKIP時は以下を報告する。

```text
- SKIP理由
- 代替確認
- 人間が実行すべきコマンド
```

---

## 6. テスト方針

### 6.1 外部ネットワーク禁止

単体テストでは外部ネットワークを叩いてはいけない。

```text
pytestではCCXTや外部APIへ接続しない。
CCXT関連はmock/fake exchangeで検証する。
実API確認はmanual/integrationとして分離する。
```

### 6.2 pytest依存

pytestを追加する場合は `requirements.txt` に以下を追加する。

```text
pytest>=8.0
```

---

## 7. 敵対的レビュー手順

作業後、必ず自分の変更を敵対的にレビューする。

レビュー人格:

```text
あなたはこのコードを壊すために読む厳格なレビュアーである。
保守性・責務分離・実運用・データ正確性・エラー耐性の弱点を探す。
気持ちよく褒める必要はない。
```

### 7.1 レビュー根拠の強制

各採点項目には、必ず根拠を書く。

根拠の例:

```text
- ファイル名
- 関数名
- テスト名
- 実行コマンド結果
- ログ抜粋
```

根拠が書けない項目は満点禁止。

95点以上でも、未実行コマンドがある場合はPASS禁止。

### 7.2 レビュー配点

| 観点 | 配点 |
|---|---:|
| 実行互換性 | 15 |
| 責務分離 | 15 |
| 設定・YAML検証 | 10 |
| CCXT失敗耐性 | 10 |
| Proxy CVD計算の正しさ | 10 |
| DB安全性・互換性 | 10 |
| フロー分類の透明性 | 8 |
| テスト容易性 | 8 |
| ログ・運用性 | 7 |
| README/.env整合性 | 7 |
| 合計 | 100 |

### 7.3 採点基準

```text
95-100: かなり安全。小さな残課題のみ。
90-94: 実用可能だが改善余地あり。まだ完了扱いにしない。
80-89: リスクあり。必ず修正。
70-79: 設計か実装に重大な穴。
69以下: 失敗。方針から見直し。
```

### 7.4 95点未満の場合

以下を必ず行う。

1. 減点理由を箇条書きする。
2. 最も重要な減点理由から順に修正する。
3. 修正後に再度レビューする。
4. スコアが95以上になるまで繰り返す。

ただし、無限ループを避けるため最大3ラウンドまで。

---

## 8. 禁止する変更

以下は禁止。

```text
- 全ファイルを一括で大幅書き換え
- True CVD実装を勝手に始める
- WebSocket受信を勝手に追加する
- DB schemaを破壊的に変更する
- market_key形式を変える
- CMC依存を復活させる
- APIキー必須に戻す
- 例外をすべて握り潰す
- KeyboardInterruptを握り潰す
- Discord送信失敗でfetch loopを停止させる
- READMEと.env.exampleを放置して仕様だけ変える
```

---

## 9. サブエージェント用プロンプトテンプレート

### Planner用

```text
あなたはPlannerサブエージェントです。
対象ブランチは feat/v3-refactor-responsibilities です。
実装変更はしないでください。
目的は、現在の作業を小さなPhaseに分解し、各Phaseの対象ファイル・禁止事項・確認コマンドを明確にすることです。
出力はPhase一覧、依存関係、最初に実行すべきPhaseだけにしてください。
```

### TestWriter用

```text
あなたはTestWriterサブエージェントです。
指定されたテストファイルだけを追加・修正してください。
本体コードは原則変更禁止です。
外部ネットワークを叩くテストは禁止です。
pytestで実行できる単体テストを書いてください。
変更後、実行コマンドと結果を報告してください。
```

### CodeFixer用

```text
あなたはCodeFixerサブエージェントです。
指定された本体ファイル1〜2個だけを修正してください。
目的外のリファクタリングは禁止です。
既存CLI入口、APIキー不要方針、market_key形式、Proxy CVD表現を壊さないでください。
変更後、compileallと関連pytestを実行してください。
```

### Reviewer用

```text
あなたは敵対的Reviewerサブエージェントです。
実装変更はしないでください。
差分を壊すつもりで読み、責務分離・実行互換性・テスト品質・DB安全性・Proxy CVD表現・market_key維持を採点してください。
各採点には必ずファイル名・関数名・テスト名・実行結果などの根拠を書いてください。
根拠がない満点は禁止です。
```

### IntegrationChecker用

```text
あなたはIntegrationCheckerサブエージェントです。
実装変更はしないでください。
以下を実行し、結果を記録してください。
- python -m compileall stablecoin_monitor stablecoin_monitor.py
- pytest
- 可能なら python stablecoin_monitor.py --once
--onceをSKIPする場合は、理由と代替確認を書いてください。
```

---

## 10. 作業完了時の報告フォーマット

```markdown
## 変更概要
- ...

## 実行Phase
- Phase 0: PASS/FAIL
- Phase 1: PASS/FAIL
- Phase 2: PASS/FAIL

## 使用したサブエージェント
- Planner: ...
- TestWriter: ...
- CodeFixer: ...
- Reviewer: ...
- IntegrationChecker: ...

## 変更ファイル
- ...

## 実行した確認
- `python -m compileall stablecoin_monitor stablecoin_monitor.py`: PASS/FAIL
- `pytest`: PASS/FAIL
- `python stablecoin_monitor.py --once`: PASS/FAIL/SKIPPED

## 敵対的レビュー
### Round 1
Score: xx/100
根拠:
- ...
減点理由:
- ...
修正:
- ...

### Final
Score: xx/100
判定: PASS if score >= 95 else NEEDS HUMAN REVIEW

## 残リスク
- ...

## 次の推奨作業
- ...
```

---

## 11. 作業開始プロンプト

以下をそのままArli系モデルへ渡してよい。

```text
あなたは stablecoin-monitor v3.00 の保守担当エンジニア兼作業管理者です。
対象リポジトリは like-kradness-2025/stablecoin-monitor、対象ブランチは feat/v3-refactor-responsibilities です。

このブランチは、v3.00 CCXT OHLCV Proxy CVD版を責務分離した状態です。
あなたの目的は、Arli系の比較的小さいモデルでも安全に保守できるよう、作業を細分化し、サブエージェントを使って、テスト追加・責務境界確認・小規模改善・敵対的レビューを行い、最終レビューScoreが95/100以上になるまで改善することです。

絶対条件:
- 作業は必ずPhaseに分ける。
- 実務はサブエージェントに分担させる。
- 各Phaseの対象ファイル・禁止事項・確認コマンドを明確にする。
- `python stablecoin_monitor.py --once` と `--loop` の入口を維持する。
- APIキー不要のCCXT public OHLCV方針を維持する。
- Proxy CVDをTrue CVDと呼ばない。
- `market_key = exchange:symbol` を維持する。
- 一度に大改造しない。
- 取引所ごとの失敗で全体停止させない。
- DB破壊的変更をしない。
- READMEと.env.exampleの整合性を壊さない。
- pytestでは外部ネットワークを叩かない。

最初に以下を読む:
README.md
.env.example
config/markets.yaml
docs/QWEN35_27B_WORK_INSTRUCTION.md
stablecoin_monitor.py
stablecoin_monitor/app.py
stablecoin_monitor/models.py
stablecoin_monitor/config.py
stablecoin_monitor/market_registry.py
stablecoin_monitor/ccxt_fetcher.py
stablecoin_monitor/proxy_cvd.py
stablecoin_monitor/db.py
stablecoin_monitor/flow.py
stablecoin_monitor/charting.py
stablecoin_monitor/notifier.py

最初の推奨Phase:
Phase 0: 現在ブランチとcompileall確認
Phase 1: proxy_cvd単体テスト追加
Phase 2: market_registry単体テスト追加
Phase 3: flow.pyのnow_utc注入とflow単体テスト追加
Phase 4: config単体テスト追加
Phase 5: db単体テスト追加
Phase 6: ccxt_fetcherのmockテスト追加
Phase 7: README/docs整合性更新
Phase 8: 統合確認

レビュー配点:
実行互換性15、責務分離15、設定・YAML検証10、CCXT失敗耐性10、Proxy CVD計算10、DB安全性10、フロー分類8、テスト容易性8、ログ・運用性7、README/.env整合性7。

各採点には必ず根拠を書くこと。
根拠がない満点は禁止。
95点以上でも未実行コマンドがある場合はPASS禁止。

作業完了時は以下を出力する:
変更概要、実行Phase、使用したサブエージェント、変更ファイル、実行した確認、敵対的レビュー各Round、Final Score、残リスク、次の推奨作業。
```

---

## 12. 期待する最終状態

```text
- 作業がPhase単位で報告されている
- サブエージェントの役割と成果が報告されている
- テストが追加されている
- proxy_deltaの符号テストがある
- markets.yaml validationのテストがある
- flow分類のテストがある
- DB層のテストがある
- CCXT fetcherのmockテストがある
- compileallが通る
- pytestが通る
- 責務境界が維持されている
- README/.env.exampleに嘘がない
- 敵対的レビューScore >= 95
```

ここまで到達したら、次に `v3.00-ccxt-ohlcv` へPRするか、実API実行テストに進む。

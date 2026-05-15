# Qwen3.5-27B向け 作業指示書

対象リポジトリ: `like-kradness-2025/stablecoin-monitor`

対象ブランチ: `feat/v3-refactor-responsibilities`

目的: `stablecoin-monitor v3.00` のCCXT OHLCV版を、比較的小さいモデルでも安全に継続改善できるよう、責務分離・品質確認・敵対的レビューを段階的に進める。

---

## 0. 最重要ルール

あなたはこの作業を、**小さな変更単位で進める保守担当エンジニア**として行う。

絶対に守ること。

1. 一度に大改造しない。
2. 既存の実行コマンドを壊さない。
3. `python stablecoin_monitor.py --once` を入口として維持する。
4. `python stablecoin_monitor.py --loop` を入口として維持する。
5. APIキー不要のCCXT public OHLCV方針を維持する。
6. Proxy CVDをTrue CVDと誤表現しない。
7. Symbol単位ではなく `exchange:symbol` の `market_key` を監視単位にする。
8. 取引所ごとの失敗で全体停止させない。
9. レビューで95点未満なら、必ず改善して再レビューする。
10. 変更後は必ず「何を変えたか」「なぜ変えたか」「残リスク」を報告する。

---

## 1. 現在の設計前提

現在のv3系は以下の目的を持つ。

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

このv3.00は **True CVDではない**。

```text
Proxy CVD = OHLCVから推定した売買圧
True CVD  = 約定ごとのtaker buy / taker sell累積
```

この区別を崩してはいけない。

---

## 2. 現在のモジュール構成

以下の責務分離を維持・強化する。

```text
stablecoin_monitor.py        # CLI入口、argparse、loop制御のみ
stablecoin_monitor/
  __init__.py
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

### 責務境界

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

## 3. 作業ゴール

今回の作業ゴールは、以下を満たすこと。

### 必須ゴール

1. モジュール責務がさらに明確になる。
2. 起動フローが読みやすくなる。
3. エラー発生箇所がログで特定しやすくなる。
4. テストしやすい純粋関数が増える。
5. 小さいモデルでも安全に追加修正できる構造になる。
6. 既存のREADMEと`.env.example`に矛盾を作らない。
7. `config/markets.yaml`の市場設定を壊さない。

### 非ゴール

今回やらないこと。

```text
True CVD WebSocket実装
取引所別private API対応
売買注文機能
大規模UI刷新
DB破壊的migration
非同期化の全面導入
バックテスト機能
```

---

## 4. 最初に行う確認

作業前に必ず以下を確認する。

```bash
git status
git branch --show-current
```

期待ブランチ:

```text
feat/v3-refactor-responsibilities
```

もし違うブランチなら作業を止めて、正しいブランチへ切り替える。

```bash
git checkout feat/v3-refactor-responsibilities
```

---

## 5. 最初に読むファイル

順番に読むこと。

```text
README.md
.env.example
config/markets.yaml
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
```

読みながら以下をメモする。

```text
- 実行入口はどこか
- Settingsはどこで作られるか
- markets.yamlはどこで検証されるか
- OHLCV取得はどこか
- proxy_delta算出はどこか
- DB保存はどこか
- flow分類はどこか
- chart生成はどこか
- Discord送信はどこか
```

---

## 6. 推奨する作業順序

### Phase A: 静的な構造確認

以下を確認する。

1. import循環がないか。
2. `models.py` が肥大化しすぎていないか。
3. `app.py` が低レベル処理を持ちすぎていないか。
4. `db.py` がビジネス判断を持っていないか。
5. `charting.py` がflow分類に依存しすぎていないか。
6. `flow.py` が現在時刻に依存しすぎてテストしづらくないか。

改善候補があれば、1回の変更では最大2ファイルまでにする。

---

### Phase B: テスト追加

最低限、以下のテストを追加する。

推奨ディレクトリ:

```text
tests/
```

推奨ファイル:

```text
tests/test_proxy_cvd.py
tests/test_market_registry.py
tests/test_flow.py
tests/test_config.py
```

#### test_proxy_cvd.py

必須ケース。

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

#### test_market_registry.py

必須ケース。

```text
1. 正常なmarkets.yamlを読める
2. market_key重複でConfigError
3. market_keyとexchange:symbol不一致でConfigError
4. base == quote でConfigError
5. 不正categoryでConfigError
6. enabled=false市場はenabled_marketsから除外される
```

#### test_flow.py

必須ケース。

```text
1. btc_stable deltaが閾値超え、fiat_delta >= 0 → BTC_DEMAND
2. stable_fiat deltaが閾値超え、btc_delta弱い → STABLE_INFLOW
3. stable_cross deltaが閾値超え、btc_delta弱い → STABLE_ROTATION
4. btc_stable deltaが負方向閾値超え → BTC_RISK_OFF
5. btc_stable負、stable_fiat負 → FIAT_EXIT
6. どれでもない → MIXED
```

#### test_config.py

必須ケース。

```text
1. .envなしでもデフォルトSettingsが作れる
2. OHLCV_LIMIT < 2 でConfigError
3. 相対パスがbase_dir基準でresolveされる
```

---

### Phase C: 実行確認

以下を実行する。

```bash
python -m compileall stablecoin_monitor stablecoin_monitor.py
```

可能なら実行する。

```bash
python stablecoin_monitor.py --once
```

ネットワークや取引所制限で失敗した場合も、以下を確認する。

```text
- ConfigErrorなのか
- CCXT fetch失敗なのか
- market未対応なのか
- DB schema失敗なのか
- chart生成失敗なのか
```

失敗理由を曖昧に報告しない。

---

## 7. 敵対的レビュー手順

作業後、必ず自分の変更を敵対的にレビューする。

レビュー人格:

```text
あなたはこのコードを壊すために読む厳格なレビュアーである。
保守性・責務分離・実運用・データ正確性・エラー耐性の弱点を探す。
気持ちよく褒める必要はない。
```

### レビュー観点

以下を100点満点で採点する。

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

### 採点基準

```text
95-100: かなり安全。小さな残課題のみ。
90-94: 実用可能だが改善余地あり。まだ完了扱いにしない。
80-89: リスクあり。必ず修正。
70-79: 設計か実装に重大な穴。
69以下: 失敗。方針から見直し。
```

### 95点未満の場合

以下を必ず行う。

1. 減点理由を箇条書きする。
2. 最も重要な減点理由から順に修正する。
3. 修正後に再度レビューする。
4. スコアが95以上になるまで繰り返す。

ただし、無限ループを避けるため最大3ラウンドまで。

3ラウンド後に95未満なら、以下を報告する。

```text
- 最終スコア
- 95に届かない理由
- 人間判断が必要な点
- 次にやるべき最小修正
```

---

## 8. 敵対的レビューで特に疑うべき点

### 8.1 Proxy CVDの誤表現

悪い例:

```text
CVDを取得しました
本物CVDを計算しました
```

正しい例:

```text
OHLCV由来のProxy CVDを算出しました
True CVDではありません
```

---

### 8.2 market_key崩壊

悪い例:

```text
BTC/USDTだけで保存する
```

正しい例:

```text
binance:BTC/USDT
bybit:BTC/USDT
okx:BTC/USDT
```

取引所差を潰さない。

---

### 8.3 取引所エラーで全体停止

悪い例:

```python
for market in markets:
    bars += fetch_market(market)  # 例外で全停止
```

正しい例:

```python
for market in markets:
    try:
        bars += fetch_market(market)
    except Exception:
        log and continue
```

ただし `KeyboardInterrupt` は握りつぶさない。

---

### 8.4 DB破壊

悪い例:

```sql
DROP TABLE snapshots;
```

正しい例:

```text
v2互換テーブル snapshots は残す
新規テーブル追加は非破壊
```

---

### 8.5 時刻処理

悪い例:

```text
JST文字列でDB保存
```

正しい例:

```text
DB保存はUTC ISO形式
表示だけJST
```

---

### 8.6 テスト不能な現在時刻依存

`flow.py` が `datetime.now()` に強く依存している場合、将来的にテストしづらい。

改善候補:

```python
def category_recent_delta(..., now_utc: datetime | None = None):
    now_utc = now_utc or datetime.now(UTC)
```

ただし変更は小さく行う。

---

## 9. 推奨する最初の改善タスク

### Task 1: テスト基盤追加

1. `tests/` を作成。
2. `pytest` を使うなら `requirements.txt` に `pytest>=8.0` を追加。
3. `test_proxy_cvd.py` を追加。
4. `test_market_registry.py` を追加。
5. `test_flow.py` を追加。
6. `python -m compileall stablecoin_monitor stablecoin_monitor.py` を実行。
7. `pytest` を実行。

この段階では本体コードの変更を最小限にする。

---

### Task 2: flow.py の時刻注入対応

目的:

```text
フロー分類をテストしやすくする
```

変更案:

```python
def category_recent_delta(..., now_utc: datetime | None = None):
    now_utc = now_utc or datetime.now(UTC)
    cutoff = now_utc - timedelta(minutes=lookback_minutes)
```

`classify_flow()` も必要なら `now_utc` を受け取れるようにする。

---

### Task 3: READMEにリファクタ後構成を追記

READMEに以下を追加する。

```text
## 内部構成

stablecoin_monitor.py はCLI入口のみ。
実処理は stablecoin_monitor/ 配下へ分割。
```

---

## 10. 禁止する変更

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

## 11. 出力フォーマット

作業完了時は、以下の形式で報告する。

```markdown
## 変更概要
- ...

## 変更ファイル
- ...

## 実行した確認
- `python -m compileall stablecoin_monitor stablecoin_monitor.py`: PASS/FAIL
- `pytest`: PASS/FAIL
- `python stablecoin_monitor.py --once`: PASS/FAIL/SKIPPED

## 敵対的レビュー
### Round 1
Score: xx/100
減点理由:
- ...
修正:
- ...

### Round 2
Score: xx/100
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

## 12. 作業開始プロンプト

以下をそのまま作業モデルへ渡してよい。

```text
あなたは stablecoin-monitor v3.00 の保守担当エンジニアです。
対象リポジトリは like-kradness-2025/stablecoin-monitor、対象ブランチは feat/v3-refactor-responsibilities です。

このブランチは、v3.00 CCXT OHLCV Proxy CVD版を責務分離した状態です。
あなたの目的は、比較的小さいモデルでも安全に保守できるよう、テスト追加・責務境界確認・小規模改善・敵対的レビューを行い、最終レビューScoreが95/100以上になるまで改善することです。

絶対条件:
- `python stablecoin_monitor.py --once` と `--loop` の入口を維持する。
- APIキー不要のCCXT public OHLCV方針を維持する。
- Proxy CVDをTrue CVDと呼ばない。
- `market_key = exchange:symbol` を維持する。
- 一度に大改造しない。
- 取引所ごとの失敗で全体停止させない。
- DB破壊的変更をしない。
- READMEと.env.exampleの整合性を壊さない。

最初に以下を読む:
README.md
.env.example
config/markets.yaml
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

最初の推奨タスク:
1. tests/ を作成する。
2. proxy_cvd / market_registry / flow / config の単体テストを追加する。
3. 必要なら pytest を requirements.txt に追加する。
4. `python -m compileall stablecoin_monitor stablecoin_monitor.py` を実行する。
5. `pytest` を実行する。
6. 変更後、敵対的レビューを100点満点で行う。
7. Scoreが95未満なら、減点理由を修正して再レビューする。
8. 最大3ラウンドまで継続する。
9. 最終結果を指定フォーマットで報告する。

レビュー配点:
実行互換性15、責務分離15、設定・YAML検証10、CCXT失敗耐性10、Proxy CVD計算10、DB安全性10、フロー分類8、テスト容易性8、ログ・運用性7、README/.env整合性7。

作業完了時は以下を出力する:
変更概要、変更ファイル、実行した確認、敵対的レビュー各Round、Final Score、残リスク、次の推奨作業。
```

---

## 13. 期待する最終状態

最低限、以下なら合格。

```text
- テストが追加されている
- proxy_deltaの符号テストがある
- markets.yaml validationのテストがある
- flow分類のテストがある
- compileallが通る
- pytestが通る
- 責務境界が維持されている
- README/.env.exampleに嘘がない
- 敵対的レビューScore >= 95
```

ここまで到達したら、次に `v3.00-ccxt-ohlcv` へPRするか、実API実行テストに進む。

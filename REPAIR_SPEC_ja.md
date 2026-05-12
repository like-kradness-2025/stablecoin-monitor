# stablecoin-monitor 修復仕様書（1分取得 / 5分描画の分離）

## 0. 目的

`stablecoin-monitor` の現行実装は、`run_once()` が **API取得・SQLite保存・履歴読込・チャート生成・Discord送信** を1回の周期でまとめて実行している。  
そのため `--loop` 実行時は実質的に「同じ周期でデータ収集と可視化を行う」構造になっている。

本修復の目的は、以下を明確に分離すること。

- **1分ごと**: CoinMarketCap から最新データを取得し、SQLite に保存する
- **5分ごと**: SQLite の履歴を読み出してチャートを生成し、必要なら Discord に送信する

本書は実装前レビュー用の修復仕様であり、実装案・制御条件・移行手順・検証方法を固定する。

---

## 1. 現状の問題分析

### 1.1 現行の結合点
ソース確認結果（`stablecoin_monitor.py`）より、以下が1本化されている。

- `run_once(settings, session)` 内で以下を直列実行
  1. `fetch_quotes_latest()`
  2. `save_snapshot()`
  3. `prune_db()`
  4. `load_history()`
  5. `make_chart()`
  6. `build_summary()`
  7. `send_discord()`（任意）
- `main()` の `--loop` は単一の `poll_interval_seconds` で `run_once()` を繰り返す
- `Settings.poll_interval_seconds` は現状 `POLL_INTERVAL_SECONDS` のみで決まる
- チャートは保存直後の履歴を前提に生成されるため、取得周期と描画周期が一致している

### 1.2 問題の影響
- 1分粒度の履歴が蓄積されないため、短期乖離の把握が弱い
- 5分単位の更新では、チャートの情報密度が低くなる
- 取得失敗と描画失敗が同一サイクルに閉じているため、片方の障害が他方に波及しやすい
- SQLite の読み書きが同一周期に集約され、将来の並列化時にロック競合が起きやすい

### 1.3 仕様上のゴールとの差分
目標は以下の分離である。

- **Fetch**: 60秒周期で保存のみ
- **Render**: 300秒周期でチャート生成・通知

したがって、実行責務を「1分の記録」と「5分の可視化」に分ける必要がある。

---

## 2. 目標アーキテクチャ

### 2.1 設計方針
- 既存ロジックを極力流用する
- スキーマ変更は原則しない
- 関数の責務を分離し、周期制御だけを追加する
- 取得処理が描画処理に依存しない構造にする
- 将来的なプロセス分離に耐える形にする

### 2.2 構成案
#### A. Fetch path
- `fetch_quotes_latest()` で最新価格を取得
- `save_snapshot()` で SQLite に保存
- `prune_db()` を必要な頻度で実行
- 失敗時は保存せず終了、次回周期へ進む

#### B. Render path
- `load_history()` は read-only transaction を開始した直後に実行する
- `load_history()` は必要な rows を読み切った時点で transaction を閉じる
- `make_chart()` と `build_summary()` は transaction 外の純粋処理とし、受け取った rows だけで完結する
- これにより render は一貫した commit 済み履歴を読みつつ、読み取りロックは短時間で解放される
- `send_discord()` はチャート生成成功後のみ実行

#### C. Scheduler / Coordinator
- 1分ループと5分ループを同一プロセス内で管理
- 各タスクは独立した次回実行時刻を持つ
- 片方の失敗で他方を停止させない

### 2.3 実装の分割点
新設・分割対象は以下。

- `fetch_and_store_snapshot()`
  - 取得、保存、必要なら prune を担当
- `render_and_notify_chart()`
  - 履歴読込、描画、通知を担当
- `run_loop()` もしくは `main()` 内のスケジューラ
  - 1分/5分のタイマー管理を担当

既存の以下は再利用する。

- `fetch_quotes_latest()`
- `save_snapshot()`
- `prune_db()`
- `load_history()`
- `make_chart()`
- `build_summary()`
- `send_discord()`

---

## 3. 制御フロー

### 3.1 単一プロセス内の推奨制御
現行の `--loop` を維持しつつ、内部を2系統に分ける。

#### 1分タスク
1. `next_fetch_at` に到達したら fetch 実行
2. API から最新値取得
3. SQLite へ保存
4. 必要なら古い行を削除
5. `next_fetch_at += 60秒`（絶対時刻基準）

#### 5分タスク
1. `next_render_at` に到達したら render 実行
2. SQLite から履歴取得
3. PNG を生成
4. Discord 送信（設定時のみ）
5. `next_render_at += 300秒`（絶対時刻基準）

### 3.2 絶対時刻ベースの理由
- 実行時間のぶれで周期が徐々に遅延するのを防ぐ
- `sleep(固定秒)` よりも、長時間稼働時の位相ズレが小さい

### 3.3 実行境界の固定
- `--loop` は `next_fetch_at` と `next_render_at` を別々に持つ
- 1分タスクと5分タスクは同じループ内で順に判定し、到達していない側は実行しない
- `next_fetch_at` は 60秒刻み、`next_render_at` は 300秒刻みで、いずれも `time.monotonic()` 基準で更新する
- どちらか一方の処理時間が長くても、もう一方のスケジュール位相はずらさない

### 3.4 起動直後の処理順
- DB 初期化
- Fetch/Render の次回実行時刻を「現在時刻の次の境界」に揃える
- **起動直後に fetch も render も即時実行しない**
- 初回の fetch/render は、次の 1 分境界 / 5 分境界に到達してから実行する
- これにより、再起動直後だけ実行間隔が短くなる挙動をなくす

---

## 4. 設定変数

### 4.1 新規・変更する設定
#### 新規
- `FETCH_INTERVAL_SECONDS=60`
  - 1分取得周期
- `RENDER_INTERVAL_SECONDS=300`
  - 5分描画周期

#### 既存の継続利用
- `CMC_API_KEY`
- `DISCORD_WEBHOOK_URL`
- `DB_PATH`
- `OUTPUT_DIR`
- `HISTORY_DAYS`
- `RETENTION_DAYS`
- `BTC_SYMBOL`
- `STABLE_SYMBOLS`
- `DEVIATION_ALERT_BP`
- `REQUEST_TIMEOUT_SECONDS`

#### 互換維持用
- `POLL_INTERVAL_SECONDS`
  - 旧設定として受ける
  - 新設定が未指定のときのフォールバックとしてのみ使用
  - 将来的には廃止候補

### 4.2 優先順位
1. `FETCH_INTERVAL_SECONDS` / `RENDER_INTERVAL_SECONDS`
2. `POLL_INTERVAL_SECONDS`（互換フォールバック）
3. ハードコード既定値

### 4.3 妥当性チェック
- `FETCH_INTERVAL_SECONDS >= 10`
- `RENDER_INTERVAL_SECONDS >= FETCH_INTERVAL_SECONDS`
- `RENDER_INTERVAL_SECONDS >= 60`
- `HISTORY_DAYS >= 1`
- `RETENTION_DAYS >= 1`
- `STABLE_SYMBOLS` は空不可
- `BTC_SYMBOL` は `STABLE_SYMBOLS` と重複不可

### 4.4 既定値の扱い
- Fetch: 60秒
- Render: 300秒
- `POLL_INTERVAL_SECONDS` の既定値は互換用に残してよいが、内部制御では新変数を優先する

---

## 5. SQLite / WAL の考慮事項

### 5.1 なぜ WAL が必要か
1分取得と5分描画を分離すると、同時に SQLite に接続する機会が増える。  
通常の journaling のままだと、読み書きが重なった際に `database is locked` の発生確率が上がる。

### 5.2 推奨設定
各接続で以下を設定する。

- `PRAGMA journal_mode=WAL`
- `PRAGMA synchronous=NORMAL`
- `PRAGMA busy_timeout=<設定値>`

### 5.3 具体的な実装方針
- DB 接続ヘルパーを設け、接続ごとに PRAGMA を適用する
- `save_snapshot()` 用の書き込み接続と `load_history()` 用の読み取り接続を分ける
- 書き込み接続では `journal_mode=WAL` と `synchronous=NORMAL` を適用する
- 読み取り接続では read-only 接続を使い、`busy_timeout=5000` を適用する
- 書き込みは短時間で完了させる
- 読み取りは長時間トランザクションにしない
- `save_snapshot()` と `load_history()` はそれぞれ短い接続で完了させる

### 5.4 WAL の副作用と対策
- `-wal` / `-shm` ファイルが生成される
- バックアップ時は DB 本体だけでなく WAL も考慮する
- 長時間の読み取りや停止で WAL が肥大化する可能性がある
- 必要なら運用で checkpoint を実施する

### 5.5 prune との関係
- `prune_db()` は保存直後の軽い削除に留める
- `VACUUM` は通常ループ内で行わない
- 大量削除が必要なら別ジョブ化する

---

## 6. エラーハンドリング

### 6.1 Fetch 失敗
対象:
- HTTP エラー
- タイムアウト
- JSON 解析失敗
- CMC の `status.error_code`
- 必須シンボル欠損

方針:
- その周期の保存は行わない
- Render タスクは止めない
- ログに原因、HTTP ステータス、再試行結果を残す

### 6.2 Render 失敗
対象:
- DB 読み込み失敗
- 履歴不足や型不整合
- Matplotlib エラー
- ファイル保存失敗
- Discord 送信失敗

方針:
- Fetch タスクは止めない
- **render 前段**（DB 読み込み・履歴整形・Matplotlib・ファイル保存）で失敗した場合は、チャート生成と Discord 送信を行わず、ログに残して次周期へ進む
- **Discord 送信失敗** は、チャート生成は成功したが通知だけ失敗した扱いにする
- 既存画像がある場合は削除しない
- render は SQLite の read-only トランザクションで、直近の commit 済み履歴を読む
- その read-only 取得に失敗した場合は画像を作らず、ログに残して次周期へ進む
- ログに失敗対象を明記する

### 6.3 リトライ
- API 通信には既存の Retry を継続利用する
- ただし1サイクルの総実行時間が周期を食い潰さないよう、再試行上限とタイムアウトを維持する
- 目安として、Fetch1回の実行が 60秒を大きく超えないようにする

### 6.4 SQLite エラー
- `database is locked` は WAL と busy_timeout で抑制する
- それでも失敗した場合は次周期で再試行
- 連続失敗回数をログに出す

### 6.5 フェイルセーフ
- 取得失敗で既存履歴を壊さない
- 描画失敗で DB 内容を変更しない
- どちらか一方の例外が全体停止に波及しないようにする

---

## 7. エッジケース

### 7.1 起動直後に履歴が少ない
- 1点だけでも描画できること
- まだ履歴が足りない場合は、線が短くても落とさないこと
- `build_summary()` は前回値がない場合も表示できること

### 7.2 同一タイムスタンプの重複
- 既存スキーマは `PRIMARY KEY (ts_utc, symbol)`
- 同一分に再実行しても上書き可能
- 保存時刻は秒切り捨てで統一するのが望ましい

### 7.3 時刻変更
- NTP 補正や手動変更でシステム時刻が飛んでも、ループが暴走しないこと
- 次回実行時刻の計算は `time.monotonic()` と `datetime` を使い分ける
  - 待機計測は monotonic
  - 境界判定は UTC 時刻

### 7.4 API レスポンス欠損
- 必須シンボルが1つでも欠けた場合は、そのFetch周期を失敗扱いにする
- 部分保存は行わない
- 中途半端な履歴を作らない

### 7.5 長時間停止後の再開
- 未実行分を一括追従しない
- 起動後は次の境界から通常運転へ復帰する

### 7.6 ディスク逼迫
- 1分保存によりDB増加速度が上がる
- `RETENTION_DAYS` の既定値と運用容量を再確認する
- 必要なら保持期間を短縮する

---

## 8. 移行計画

### 8.1 ステップ1: 関数分離
- `run_once()` をそのままスケジュールの単位にしない
- Fetch と Render の関数を分ける
- 既存処理の流用を優先し、ロジックの変更は最小化する

### 8.2 ステップ2: 設定追加
- `.env.example` が存在しない場合は新規作成し、`FETCH_INTERVAL_SECONDS` と `RENDER_INTERVAL_SECONDS` を追加する
- README の説明を更新し、実際の配布物と矛盾しないようにする
- `POLL_INTERVAL_SECONDS` は後方互換のため残す

### 8.3 ステップ3: スケジューラ実装
- `--loop` の動作を二重周期に切り替える
- `--once` は保守目的で残してよい
- `--once` の意味は **「fetch 1回 + render 1回」** に固定する
- `--once` は、分離後の部品を1回ずつ呼ぶ検証用経路として扱う
- `--once` では fetch 成功時のみ render を実行する。fetch 失敗時は render を試みず、終了コード 1 で終える
- `--once` で render が失敗した場合は終了コード 1 とし、その回のチャート生成・通知は行わない（`save_snapshot()` 済みの DB 変更は残る）

### 8.4 ステップ4: DB 強化
- WAL 有効化
- `busy_timeout=5000` を既定値にする
- 接続ヘルパー導入

### 8.5 ステップ5: 段階的切替
- ローカルで1分保存が増えることを確認
- 次に5分描画が1分データを参照していることを確認
- 最後に Discord 通知を有効化する

### 8.6 ロールバック
- 重大障害時は `POLL_INTERVAL_SECONDS=300` 相当の旧挙動に戻せるようにしておく
- 旧ループを完全削除するのは、新方式の安定確認後に限定する

---

## 9. 検証計画

### 9.1 単体検証
- 設定読み込みが新旧変数を正しく解釈する
- Fetch 関数がSQLite保存のみを担う
- Render 関数がDB履歴のみから画像を生成する

### 9.2 結合検証
- 6〜10分程度動かし、DBに1分間隔相当のレコードが増えることを確認
- 5分ごとにPNGが更新されることを確認
- 5分チャートに1分粒度の履歴が反映されることを確認

### 9.3 競合検証
- Fetch と Render を同時に走らせ、`database is locked` が再現しないことを確認
- WAL 有効時に読み書きが干渉しにくいことを確認

### 9.4 異常系検証
- CMC API を意図的に失敗させ、Fetch だけが失敗することを確認
- DB を一時的に書き込み不可にし、Fetch / Render のどちらが失敗するかを切り分ける
- Discord webhook 無効時にローカル保存だけ継続することを確認

### 9.5 性能検証
- Fetch 1回の実行時間が60秒周期を圧迫しないこと
- Render 1回の実行時間が300秒周期を越えないこと
- 履歴増加に伴う描画時間を記録する

### 9.6 受け入れ基準
- 1分ごとに SQLite へスナップショットが保存される
- 5分ごとにチャートが生成される
- Fetch 失敗が Render を止めない
- Render 失敗が Fetch を止めない
- 明確な SQLite ロック頻発がない

### 9.7 配布物と設定の対応
- `.env.example` : `CMC_API_KEY`, `FETCH_INTERVAL_SECONDS`, `RENDER_INTERVAL_SECONDS`, `POLL_INTERVAL_SECONDS`, `DB_PATH`, `OUTPUT_DIR` を含める
- `README.md` : 実際に配布される設定項目と起動方法だけを記載する
- 仕様と配布物に差が出た場合は、実装より先にこの対応表を更新する

---

## 10. リスクとトレードオフ

### 10.1 リスク
- 1分保存化でDB容量とI/Oが増える
- 1プロセス内の二重スケジューラは、実装を誤ると複雑化する
- WAL により補助ファイルが増える
- 同一プロセス運用では完全な障害分離はできない

### 10.2 トレードオフ
- **最小変更**を優先すると、運用は簡単だが将来の分離拡張は弱くなる
- **プロセス分離**を先に行うと、障害分離は良いが運用と起動管理が増える
- 本件はまず **1プロセス・2周期・SQLite WAL** を採用するのが妥当

### 10.3 判断基準
- 本件の主目的は「取得密度の改善」と「可視化頻度の維持」である
- そのため、まずは最小変更で分離し、安定後にプロセス分離を検討する

---

## 11. 実装拘束条件

- Fetch 周期と Render 周期を同一変数に戻さない
- `run_once()` は、もし残すなら「保守用の一括処理」に限定する
- 既定値と README と `.env.example` を一致させる
- 1分粒度化後も既存DBを破壊しない
- スキーマ変更は避ける

---

## 12. まとめ

本修復では、`stablecoin-monitor` を **「1分で記録し、5分で描く」** 構成へ分割する。  
実装は、既存の取得・保存・描画ロジックを再利用しつつ、周期制御と SQLite 接続方針を分離するのが最小リスクである。  
SQLite は WAL 化し、Fetch と Render を独立に失敗させることで、監視の安定性を保つ。

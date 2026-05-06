# stablecoin_monitor_rebuilt

CoinMarketCap の API キーを入れるだけで動く、ステーブルコイン監視ツールです。

## 何が変わったか

- **必須設定は `CMC_API_KEY` だけ**
- **Binance 依存を削除**
- **ローカル SQLite に履歴保存**
- **履歴からチャート生成**
- **Discord 通知は任意**

つまり、最小構成ではこうです。

1. API キーを取る
2. `.env` に入れる
3. 実行する

## 監視対象

デフォルト:

- BTC
- USDT
- USDC
- FDUSD

必要なら `.env` の `STABLE_SYMBOLS` を変えるだけです。

## セットアップ

### 1. 仮想環境を作る

#### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

#### Linux / macOS / WSL / Termux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. `.env` を作る

`.env.example` を `.env` にコピーして、`CMC_API_KEY` だけ入れてください。

```env
CMC_API_KEY=your_coinmarketcap_api_key
```

Discord 通知も使うならこれも入れます。

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

## 実行

### 1回だけ実行

```bash
python stablecoin_monitor.py --once
```

### 5分ごとに常駐実行

```bash
python stablecoin_monitor.py --loop
```

## 出力

- DB: `data/stablecoin_monitor.db`
- 画像: `output/stablecoin_monitor_YYYYMMDD_HHMMSS.png`

## チャート内容（v2.00）

ダークテーマのダッシュボード風レイアウトです。

### 構造図

```
┌─────────────────────────────────────────────────────┐
│  BTC $81,xxx | USDT -x.xbp | USDC -x.xbp | FDUSD -x.xbp │  ← 最新値サマリー
├─────────────────────────────────────────────────────┤
│                    Stablecoin Monitor                │
│              BTC 価格チャート（全幅）                   │
├─────────────────────────────────────────────────────┤
│  USDT Deviation  │  USDC Deviation  │  FDUSD Deviation │  ← 横 1x3 カード
├──────────────────┼───────────────────┼──────────────────┤
│ USDT 24h volume │ USDC 24h volume  │ FDUSD 24h volume │  ← 横 1x3 カード
└──────────────────┴───────────────────┴──────────────────┘
                        右下：日時表示（JST）
```

### 詳細仕様

**全体サイズ:** `figsize=(7, 5)`

**高さ比率:** `[1.5, 0.6, 0.6]`（上段：中段：下段）

| セクション | 内容 | 備考 |
|-----------|------|------|
| 上段 | BTC 価格チャート | 全幅、オレンジ色 |
| 中段 | 乖離率（bp） | 横 1x3 カード、USDT/USDC/FDUSD |
| 下段 | 24h 出来高 | 横 1x3 カード、USDT/USDC/FDUSD |

**配色:**
- USDT: 緑色
- USDC: 青色
- FDUSD: オレンジ色

**ラベル:**
- 中段・下段の Y 軸単位ラベルは省略（視認性向上）
- 上段・中段の X 軸ラベルは省略（重複回避）
- 下段の X 軸は右端カードのみ表示

**日時:**
- 右下に JST 形式の日時表示

## アラート

`DEVIATION_ALERT_BP` を超えたら、要約文に `ALERT` が出力されます。

例:

```text
ALERT: USDT -18.2bp, FDUSD +25.4bp
```

## 環境変数設定

`.env` ファイルに以下を設定できます。

| 変数名 | 説明 | デフォルト |
|--------|------|-----------|
| `CMC_API_KEY` | CoinMarketCap API キー（必須） | - |
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL（任意） | 未設定 |
| `DB_PATH` | SQLite データベースの保存先 | `data/stablecoin_monitor.db` |
| `OUTPUT_DIR` | 画像の保存先ディレクトリ | `output` |
| `POLL_INTERVAL_SECONDS` | 監視間隔（秒） | `300`（5 分） |
| `HISTORY_DAYS` | チャートに表示する履歴期間（日） | `3` |
| `RETENTION_DAYS` | データベースの保持期間（日） | `30` |
| `BTC_SYMBOL` | BTC のシンボル | `BTC` |
| `STABLE_SYMBOLS` | 監視対象ステーブルコイン（カンマ区切り） | `USDT,USDC,FDUSD` |
| `DEVIATION_ALERT_BP` | アラート閾値（bp） | `15` |
| `REQUEST_TIMEOUT_SECONDS` | API リクエストのタイムアウト（秒） | `20` |

## PR運用

このリポジトリは、変更を push したあとに PR で確認してから取り込む運用にします。

## 注意点

- CoinMarketCap の `v2/cryptocurrency/quotes/latest` を使っています。
- `symbol` 指定では同一シンボル候補が複数返ることがあるため、スクリプト側で **順位と時価総額を見て最有力候補を選ぶ** 形にしています。
- Discord webhook は任意です。未設定ならローカル保存だけ行います。

## すぐ使うための最短手順

```bash
cp .env.example .env
# .env を開いて CMC_API_KEY だけ入れる
python stablecoin_monitor.py --once
```

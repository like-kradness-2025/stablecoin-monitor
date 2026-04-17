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

## チャート内容

- ダークテーマのダッシュボード風レイアウト
- 上段: BTC価格
- 中段: ステーブルコインの $1 からの乖離（bp）
- 下段: 各ステーブルコインの 24h 出来高
- 右上: 最新スナップショット表
- 上部: タイムスタンプと最新値のサマリ

## アラート

`DEVIATION_ALERT_BP` を超えたら、要約文に `ALERT` が出ます。

例:

```text
ALERT: USDT -18.2bp, FDUSD +25.4bp
```

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

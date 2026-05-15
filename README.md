# stablecoin-monitor v3.00

CCXT の public OHLCV を横断取得し、取引所単位ペアの **Proxy CVD** を監視するステーブル資金フローツールです。

v2.20 の「1分取得 / 5分描画」構造を引き継ぎつつ、取得元を CoinMarketCap quotes から CCXT OHLCV に変更しています。

## v3.00 の目的

ステーブル出来高が増えたときに、以下をざっくり分類します。

- フィアットからステーブルへ流入しているのか
- ステーブル間で通貨変換しているのか
- ステーブルが BTC 買いに向かっているのか
- BTC からステーブルへ逃げているのか
- ステーブルからフィアットへ抜けているのか

本物の約定CVDではなく、OHLCVから推定した **Proxy CVD** です。

## 重要な注意

v3.00 の CVD は近似です。

```text
Proxy CVD = OHLCV の足方向・終値位置・出来高から推定した売買圧
True CVD  = 約定ごとの taker buy / taker sell を累積したもの
```

そのため、v3.00 は広域レーダーとして使います。吸収・売り枯れ・踏み上げの精密検出は、将来の True CVD 実装で扱います。

## APIキー

デフォルト構成では不要です。

CCXT の public OHLCV を使うため、取引所APIキーも CMC APIキーも不要です。

Discord通知を使う場合のみ `DISCORD_WEBHOOK_URL` を設定してください。

## 監視対象

監視対象は `config/markets.yaml` で管理します。

監視キーは以下です。

```text
market_key = exchange:symbol
例: binance:BTC/FDUSD
```

カテゴリは以下です。

| category | 意味 |
|---|---|
| `btc_stable` | BTC とステーブルのペア。BTC需要/逃避を見る |
| `stable_fiat` | ステーブルとフィアットのペア。流入/出口を見る |
| `stable_cross` | ステーブル同士のペア。通貨変換を見る |
| `other` | その他 |

## デフォルト監視ペア

### BTC / Stable

```text
binance:BTC/USDT
binance:BTC/FDUSD
binance:BTC/USDC
bybit:BTC/USDT
okx:BTC/USDT
coinbase:BTC/USDC
```

### Stable / Fiat

```text
coinbase:USDC/USD
kraken:USDT/USD
kraken:USDC/USD
kraken:USDT/EUR
```

### Stable / Stable

```text
binance:FDUSD/USDT
binance:USDC/USDT
```

## セットアップ

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Linux / macOS / WSL / Termux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## `.env` 作成

```bash
cp .env.example .env
```

最小構成では編集なしでも動きます。

Discord通知を使う場合だけ以下を入れます。

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

## 実行

### 1回だけ実行

```bash
python stablecoin_monitor.py --once
```

### 常駐実行

```bash
python stablecoin_monitor.py --loop
```

`--loop` では以下の周期で動きます。

- OHLCV取得: 60秒ごと
- チャート生成: 300秒ごと

## 主な環境変数

| 変数名 | 説明 | デフォルト |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL | 未設定 |
| `DB_PATH` | SQLite DB保存先 | `data/stablecoin_monitor.db` |
| `OUTPUT_DIR` | PNG出力先 | `output` |
| `MARKETS_CONFIG_PATH` | 監視ペア設定 | `config/markets.yaml` |
| `OHLCV_TIMEFRAME` | CCXT OHLCV時間足 | `1m` |
| `OHLCV_LIMIT` | 取得本数 | `120` |
| `FETCH_INTERVAL_SECONDS` | 取得周期 | `60` |
| `RENDER_INTERVAL_SECONDS` | 描画周期 | `300` |
| `HISTORY_DAYS` | 描画履歴日数 | `1` |
| `RETENTION_DAYS` | DB保持日数 | `30` |
| `REQUEST_TIMEOUT_SECONDS` | APIタイムアウト | `20` |
| `FLOW_DELTA_THRESHOLD_QUOTE` | フロー分類の閾値 | `1000000` |

## DB出力

v3.00 では以下のテーブルを使います。

| table | 用途 |
|---|---|
| `market_pairs` | 監視ペアのマスタ |
| `ohlcv_bars` | OHLCV + proxy_delta + proxy_cvd |
| `snapshots` | v2互換用。v3では主役ではない |

## チャート内容

出力ファイル例:

```text
output/stablecoin_monitor_v3_YYYYMMDD_HHMMSS.png
```

パネル構成:

```text
Panel 1: BTC価格
Panel 2: BTC/stable Proxy CVD
Panel 3: stable/fiat Proxy CVD
Panel 4: stable/stable Proxy CVD
```

## シグナル分類

Discord本文または標準出力に以下のようなシグナルを出します。

| Signal | 意味 |
|---|---|
| `BTC_DEMAND` | ステーブル資金がBTCへ向かうバイアス |
| `STABLE_INFLOW` | フィアットからステーブル流入、BTC反応は弱い |
| `STABLE_ROTATION` | ステーブル間の乗り換え需要 |
| `BTC_RISK_OFF` | BTCからステーブルへ逃避 |
| `FIAT_EXIT` | BTC売りとフィアット出口が同時に強い |
| `MIXED` | 混在。断定しない |

## 次の拡張候補

- Binance True CVD receiver
- Proxy CVD と True CVD の乖離検証
- Top出来高ペアの自動選定
- Bybit / OKX / Coinbase のTrue CVD追加

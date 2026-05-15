from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import requests
from zoneinfo import ZoneInfo

try:
    import ccxt  # type: ignore
except ImportError:  # pragma: no cover - handled at runtime
    ccxt = None

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - handled at runtime
    yaml = None


UTC = timezone.utc
JST = ZoneInfo('Asia/Tokyo')
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_FETCH_INTERVAL_SECONDS = 60
DEFAULT_RENDER_INTERVAL_SECONDS = 300
DEFAULT_HISTORY_DAYS = 1
DEFAULT_RETENTION_DAYS = 30
DEFAULT_OHLCV_TIMEFRAME = '1m'
DEFAULT_OHLCV_LIMIT = 120
DEFAULT_FLOW_DELTA_THRESHOLD = 1_000_000.0
ALLOWED_CATEGORIES = {'btc_stable', 'stable_fiat', 'stable_cross', 'other'}


@dataclass(slots=True)
class Settings:
    discord_webhook_url: str | None
    db_path: Path
    output_dir: Path
    poll_interval_seconds: int
    fetch_interval_seconds: int
    render_interval_seconds: int
    history_days: int
    retention_days: int
    request_timeout_seconds: int
    markets_config_path: Path
    ohlcv_timeframe: str
    ohlcv_limit: int
    flow_delta_threshold_quote: float


@dataclass(slots=True)
class MarketConfig:
    market_key: str
    exchange: str
    symbol: str
    base_symbol: str
    quote_symbol: str
    category: str
    priority: int
    enabled: bool


@dataclass(slots=True)
class OhlcvBar:
    bucket_start_utc: datetime
    market_key: str
    exchange: str
    symbol: str
    base_symbol: str
    quote_symbol: str
    category: str
    timeframe: str
    timeframe_seconds: int
    open: float
    high: float
    low: float
    close: float
    volume_base: float
    volume_quote: float
    proxy_delta_base: float
    proxy_delta_quote: float


class ConfigError(RuntimeError):
    pass


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == '':
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f'{name} must be an integer: {raw}') from exc


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == '':
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f'{name} must be a number: {raw}') from exc


def load_settings(base_dir: Path) -> Settings:
    load_dotenv(base_dir / '.env')

    output_dir = Path(os.getenv('OUTPUT_DIR', base_dir / 'output')).expanduser().resolve()
    db_path = Path(os.getenv('DB_PATH', base_dir / 'data' / 'stablecoin_monitor.db')).expanduser().resolve()

    poll_interval = env_int('POLL_INTERVAL_SECONDS', DEFAULT_INTERVAL_SECONDS)
    fetch_interval = env_int('FETCH_INTERVAL_SECONDS', env_int('MARKET_FETCH_INTERVAL_SECONDS', DEFAULT_FETCH_INTERVAL_SECONDS))
    render_interval = env_int('RENDER_INTERVAL_SECONDS', DEFAULT_RENDER_INTERVAL_SECONDS)

    markets_config_path = Path(
        os.getenv('MARKETS_CONFIG_PATH', base_dir / 'config' / 'markets.yaml')
    ).expanduser()
    if not markets_config_path.is_absolute():
        markets_config_path = (base_dir / markets_config_path).resolve()
    else:
        markets_config_path = markets_config_path.resolve()

    ohlcv_timeframe = os.getenv('OHLCV_TIMEFRAME', DEFAULT_OHLCV_TIMEFRAME).strip() or DEFAULT_OHLCV_TIMEFRAME
    ohlcv_limit = env_int('OHLCV_LIMIT', DEFAULT_OHLCV_LIMIT)
    if ohlcv_limit < 2:
        raise ConfigError('OHLCV_LIMIT must be >= 2')

    return Settings(
        discord_webhook_url=(os.getenv('DISCORD_WEBHOOK_URL') or '').strip() or None,
        db_path=db_path,
        output_dir=output_dir,
        poll_interval_seconds=poll_interval,
        fetch_interval_seconds=fetch_interval,
        render_interval_seconds=render_interval,
        history_days=env_int('HISTORY_DAYS', DEFAULT_HISTORY_DAYS),
        retention_days=env_int('RETENTION_DAYS', DEFAULT_RETENTION_DAYS),
        request_timeout_seconds=env_int('REQUEST_TIMEOUT_SECONDS', 20),
        markets_config_path=markets_config_path,
        ohlcv_timeframe=ohlcv_timeframe,
        ohlcv_limit=ohlcv_limit,
        flow_delta_threshold_quote=env_float('FLOW_DELTA_THRESHOLD_QUOTE', DEFAULT_FLOW_DELTA_THRESHOLD),
    )


def get_db_connection(db_path: Path, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_db_connection(db_path) as conn:
        # Legacy table kept for compatibility with v2.x databases.
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS snapshots (
                ts_utc TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price_usd REAL NOT NULL,
                volume_24h_usd REAL,
                market_cap_usd REAL,
                cmc_rank INTEGER,
                PRIMARY KEY (ts_utc, symbol)
            )
            '''
        )
        conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_ts ON snapshots(symbol, ts_utc)'
        )

        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS market_pairs (
                market_key TEXT PRIMARY KEY,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                base_symbol TEXT NOT NULL,
                quote_symbol TEXT NOT NULL,
                category TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 100,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            )
            '''
        )
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS ohlcv_bars (
                bucket_start_utc TEXT NOT NULL,
                market_key TEXT NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                base_symbol TEXT NOT NULL,
                quote_symbol TEXT NOT NULL,
                category TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timeframe_seconds INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume_base REAL NOT NULL,
                volume_quote REAL NOT NULL,
                proxy_delta_base REAL NOT NULL,
                proxy_delta_quote REAL NOT NULL,
                proxy_cvd_base REAL,
                proxy_cvd_quote REAL,
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY (bucket_start_utc, market_key, timeframe_seconds)
            )
            '''
        )
        conn.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_ohlcv_bars_key_bucket
            ON ohlcv_bars(market_key, bucket_start_utc)
            '''
        )
        conn.execute(
            '''
            CREATE INDEX IF NOT EXISTS idx_ohlcv_bars_category_bucket
            ON ohlcv_bars(category, bucket_start_utc)
            '''
        )
        conn.commit()


def timeframe_to_seconds(timeframe: str) -> int:
    unit = timeframe[-1]
    try:
        value = int(timeframe[:-1])
    except ValueError as exc:
        raise ConfigError(f'Unsupported OHLCV_TIMEFRAME: {timeframe}') from exc

    if unit == 'm':
        return value * 60
    if unit == 'h':
        return value * 3600
    if unit == 'd':
        return value * 86400
    raise ConfigError(f'Unsupported OHLCV_TIMEFRAME unit: {timeframe}')


def load_markets_config(path: Path) -> list[MarketConfig]:
    if yaml is None:
        raise ConfigError('PyYAML is not installed. Run: pip install -r requirements.txt')
    if not path.exists():
        raise ConfigError(f'Markets config not found: {path}')

    payload = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    raw_markets = payload.get('markets')
    if not isinstance(raw_markets, list) or not raw_markets:
        raise ConfigError('markets.yaml must contain a non-empty "markets" list')

    markets: list[MarketConfig] = []
    for idx, raw in enumerate(raw_markets, start=1):
        if not isinstance(raw, dict):
            raise ConfigError(f'markets[{idx}] must be an object')

        exchange = str(raw.get('exchange', '')).strip().lower()
        symbol = str(raw.get('symbol', '')).strip().upper()
        base_symbol = str(raw.get('base', raw.get('base_symbol', ''))).strip().upper()
        quote_symbol = str(raw.get('quote', raw.get('quote_symbol', ''))).strip().upper()
        category = str(raw.get('category', 'other')).strip().lower()
        market_key = str(raw.get('market_key', f'{exchange}:{symbol}')).strip().lower()
        enabled = bool(raw.get('enabled', True))
        priority = int(raw.get('priority', 100))

        market = MarketConfig(
            market_key=market_key,
            exchange=exchange,
            symbol=symbol,
            base_symbol=base_symbol,
            quote_symbol=quote_symbol,
            category=category,
            priority=priority,
            enabled=enabled,
        )
        markets.append(market)

    validate_markets(markets)
    return sorted(markets, key=lambda item: item.priority)


def validate_markets(markets: list[MarketConfig]) -> None:
    seen: set[str] = set()
    for market in markets:
        if not market.exchange:
            raise ConfigError('market exchange must not be empty')
        if not market.symbol or '/' not in market.symbol:
            raise ConfigError(f'invalid market symbol for {market.market_key}: {market.symbol}')
        if not market.base_symbol or not market.quote_symbol:
            raise ConfigError(f'base/quote must not be empty for {market.market_key}')
        if market.base_symbol == market.quote_symbol:
            raise ConfigError(f'base and quote must differ for {market.market_key}')
        if market.category not in ALLOWED_CATEGORIES:
            raise ConfigError(f'invalid category for {market.market_key}: {market.category}')
        expected_key = f'{market.exchange}:{market.symbol}'.lower()
        if market.market_key != expected_key:
            raise ConfigError(f'market_key must be "{expected_key}", got "{market.market_key}"')
        if market.market_key in seen:
            raise ConfigError(f'duplicate market_key: {market.market_key}')
        seen.add(market.market_key)


def sync_market_pairs(db_path: Path, markets: list[MarketConfig]) -> None:
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    with get_db_connection(db_path) as conn:
        for market in markets:
            conn.execute(
                '''
                INSERT INTO market_pairs (
                    market_key, exchange, symbol, base_symbol, quote_symbol,
                    category, enabled, priority, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_key) DO UPDATE SET
                    exchange=excluded.exchange,
                    symbol=excluded.symbol,
                    base_symbol=excluded.base_symbol,
                    quote_symbol=excluded.quote_symbol,
                    category=excluded.category,
                    enabled=excluded.enabled,
                    priority=excluded.priority,
                    updated_at_utc=excluded.updated_at_utc
                ''',
                (
                    market.market_key,
                    market.exchange,
                    market.symbol,
                    market.base_symbol,
                    market.quote_symbol,
                    market.category,
                    1 if market.enabled else 0,
                    market.priority,
                    now,
                    now,
                ),
            )
        conn.commit()


def build_exchange(exchange_id: str, settings: Settings) -> Any:
    if ccxt is None:
        raise ConfigError('ccxt is not installed. Run: pip install -r requirements.txt')
    if not hasattr(ccxt, exchange_id):
        raise ConfigError(f'ccxt does not support exchange: {exchange_id}')
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({
        'enableRateLimit': True,
        'timeout': settings.request_timeout_seconds * 1000,
    })
    return exchange


def choose_supported_timeframe(exchange: Any, preferred: str) -> str | None:
    timeframes = getattr(exchange, 'timeframes', None) or {}
    if not timeframes:
        return preferred
    if preferred in timeframes:
        return preferred
    for fallback in ('1m', '3m', '5m', '15m', '1h'):
        if fallback in timeframes:
            return fallback
    return None


def proxy_delta_from_ohlcv(open_: float, high: float, low: float, close: float, volume: float) -> float:
    if volume == 0:
        return 0.0
    if high == low:
        if close > open_:
            return volume
        if close < open_:
            return -volume
        return 0.0

    body_sign = 1.0 if close > open_ else -1.0 if close < open_ else 0.0
    clv = ((close - low) - (high - close)) / (high - low)
    score = 0.5 * body_sign + 0.5 * clv
    score = max(-1.0, min(1.0, score))
    return volume * score


def parse_ohlcv_rows(market: MarketConfig, timeframe: str, rows: list[list[float]]) -> list[OhlcvBar]:
    timeframe_seconds = timeframe_to_seconds(timeframe)
    bars: list[OhlcvBar] = []
    for row in rows:
        if len(row) < 6:
            continue
        ts_ms, open_, high, low, close, volume = row[:6]
        open_f = float(open_)
        high_f = float(high)
        low_f = float(low)
        close_f = float(close)
        volume_base = float(volume or 0.0)
        volume_quote = volume_base * close_f
        proxy_delta_base = proxy_delta_from_ohlcv(open_f, high_f, low_f, close_f, volume_base)
        proxy_delta_quote = proxy_delta_base * close_f
        bars.append(
            OhlcvBar(
                bucket_start_utc=datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC).replace(microsecond=0),
                market_key=market.market_key,
                exchange=market.exchange,
                symbol=market.symbol,
                base_symbol=market.base_symbol,
                quote_symbol=market.quote_symbol,
                category=market.category,
                timeframe=timeframe,
                timeframe_seconds=timeframe_seconds,
                open=open_f,
                high=high_f,
                low=low_f,
                close=close_f,
                volume_base=volume_base,
                volume_quote=volume_quote,
                proxy_delta_base=proxy_delta_base,
                proxy_delta_quote=proxy_delta_quote,
            )
        )
    return bars


def fetch_ohlcv_for_markets(settings: Settings, markets: list[MarketConfig]) -> list[OhlcvBar]:
    enabled_markets = [market for market in markets if market.enabled]
    bars: list[OhlcvBar] = []
    exchange_cache: dict[str, Any] = {}

    for market in enabled_markets:
        try:
            exchange = exchange_cache.get(market.exchange)
            if exchange is None:
                exchange = build_exchange(market.exchange, settings)
                exchange.load_markets()
                exchange_cache[market.exchange] = exchange

            if not exchange.has.get('fetchOHLCV'):
                logging.warning('%s does not support fetchOHLCV; skip %s', market.exchange, market.symbol)
                continue

            timeframe = choose_supported_timeframe(exchange, settings.ohlcv_timeframe)
            if timeframe is None:
                logging.warning('%s has no usable OHLCV timeframe; skip %s', market.exchange, market.symbol)
                continue

            if market.symbol not in exchange.markets:
                logging.warning('%s does not list %s; skip', market.exchange, market.symbol)
                continue

            raw_rows = exchange.fetch_ohlcv(
                market.symbol,
                timeframe=timeframe,
                limit=settings.ohlcv_limit,
            )
            parsed = parse_ohlcv_rows(market, timeframe, raw_rows)
            bars.extend(parsed)
            logging.info('Fetched %s OHLCV bars for %s', len(parsed), market.market_key)
        except KeyboardInterrupt:
            raise
        except Exception:
            logging.exception('OHLCV fetch failed for %s', market.market_key)

    return bars


def save_ohlcv_bars(db_path: Path, bars: list[OhlcvBar]) -> None:
    if not bars:
        return
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    with get_db_connection(db_path) as conn:
        conn.executemany(
            '''
            INSERT OR REPLACE INTO ohlcv_bars (
                bucket_start_utc, market_key, exchange, symbol, base_symbol,
                quote_symbol, category, timeframe, timeframe_seconds,
                open, high, low, close, volume_base, volume_quote,
                proxy_delta_base, proxy_delta_quote, proxy_cvd_base,
                proxy_cvd_quote, updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
            ''',
            [
                (
                    bar.bucket_start_utc.isoformat(),
                    bar.market_key,
                    bar.exchange,
                    bar.symbol,
                    bar.base_symbol,
                    bar.quote_symbol,
                    bar.category,
                    bar.timeframe,
                    bar.timeframe_seconds,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume_base,
                    bar.volume_quote,
                    bar.proxy_delta_base,
                    bar.proxy_delta_quote,
                    now,
                )
                for bar in bars
            ],
        )
        conn.commit()

    for market_key in sorted({bar.market_key for bar in bars}):
        recompute_proxy_cvd(db_path, market_key)


def recompute_proxy_cvd(db_path: Path, market_key: str) -> None:
    with get_db_connection(db_path) as conn:
        rows = list(
            conn.execute(
                '''
                SELECT bucket_start_utc, timeframe_seconds, proxy_delta_base, proxy_delta_quote
                FROM ohlcv_bars
                WHERE market_key = ?
                ORDER BY bucket_start_utc ASC
                ''',
                (market_key,),
            )
        )
        cvd_base = 0.0
        cvd_quote = 0.0
        for row in rows:
            cvd_base += float(row['proxy_delta_base'] or 0.0)
            cvd_quote += float(row['proxy_delta_quote'] or 0.0)
            conn.execute(
                '''
                UPDATE ohlcv_bars
                SET proxy_cvd_base = ?, proxy_cvd_quote = ?
                WHERE market_key = ? AND bucket_start_utc = ? AND timeframe_seconds = ?
                ''',
                (cvd_base, cvd_quote, market_key, row['bucket_start_utc'], row['timeframe_seconds']),
            )
        conn.commit()


def prune_db(db_path: Path, retention_days: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    cutoff_iso = cutoff.isoformat()
    deleted = 0
    with get_db_connection(db_path, read_only=False) as conn:
        for table in ('snapshots', 'ohlcv_bars'):
            column = 'ts_utc' if table == 'snapshots' else 'bucket_start_utc'
            cursor = conn.execute(f'DELETE FROM {table} WHERE {column} < ?', (cutoff_iso,))
            deleted += cursor.rowcount
        conn.commit()
    return deleted


def load_ohlcv_history(db_path: Path, days: int) -> dict[str, list[dict[str, Any]]]:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    sql = '''
        SELECT *
        FROM ohlcv_bars
        WHERE bucket_start_utc >= ?
        ORDER BY bucket_start_utc ASC
    '''
    out: dict[str, list[dict[str, Any]]] = {}
    with get_db_connection(db_path, read_only=True) as conn:
        for row in conn.execute(sql, (cutoff,)):
            item = dict(row)
            item['bucket_start_utc'] = datetime.fromisoformat(item['bucket_start_utc'])
            out.setdefault(item['market_key'], []).append(item)
    return out


def human_price(value: float) -> str:
    if abs(value) >= 1000:
        return f'${value:,.2f}'
    if abs(value) >= 1:
        return f'${value:,.6f}' if abs(value) < 10 else f'${value:,.4f}'
    return f'${value:.6f}'


def human_volume(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000_000_000:
        return f'${value / 1_000_000_000_000:.2f}T'
    if abs_value >= 1_000_000_000:
        return f'${value / 1_000_000_000:.2f}B'
    if abs_value >= 1_000_000:
        return f'${value / 1_000_000:.2f}M'
    return f'${value:,.0f}'


def apply_dashboard_theme() -> None:
    plt.style.use('dark_background')
    plt.rcParams.update({
        'figure.facecolor': '#07111f',
        'axes.facecolor': '#0d1626',
        'axes.edgecolor': '#2a3a57',
        'axes.labelcolor': '#dbe7ff',
        'xtick.color': '#9fb0c9',
        'ytick.color': '#9fb0c9',
        'text.color': '#eef4ff',
        'axes.titlecolor': '#f5f8ff',
        'grid.color': '#27405f',
        'grid.alpha': 0.28,
        'font.size': 10,
        'axes.titlesize': 11,
        'axes.labelsize': 9,
        'legend.fontsize': 7,
    })


def market_label(market_key: str) -> str:
    exchange, symbol = market_key.split(':', 1)
    return f'{exchange} {symbol.upper()}'


def pick_primary_btc_market(markets: list[MarketConfig], history: dict[str, list[dict[str, Any]]]) -> str | None:
    preferred = ['binance:btc/usdt', 'binance:btc/fdusd', 'coinbase:btc/usdc']
    for key in preferred:
        if key in history and history[key]:
            return key
    for market in sorted(markets, key=lambda item: item.priority):
        if market.category == 'btc_stable' and market.market_key in history and history[market.market_key]:
            return market.market_key
    return None


def plot_category_cvd(ax: plt.Axes, history: dict[str, list[dict[str, Any]]], markets: list[MarketConfig], category: str) -> None:
    plotted = 0
    for market in sorted(markets, key=lambda item: item.priority):
        if market.category != category:
            continue
        rows = history.get(market.market_key, [])
        if not rows:
            continue
        x = [row['bucket_start_utc'].astimezone(JST) for row in rows]
        y = [float(row['proxy_cvd_quote'] or 0.0) / 1_000_000 for row in rows]
        ax.plot(x, y, linewidth=1.0, label=market_label(market.market_key))
        plotted += 1
        if plotted >= 8:
            break
    ax.axhline(0.0, linestyle='--', linewidth=0.7, color='#a5b8d6')
    ax.set_ylabel('Proxy CVD quote mn')
    ax.grid(True, alpha=0.22)
    if plotted:
        ax.legend(loc='upper left', ncol=2, frameon=False)


def make_chart(settings: Settings, markets: list[MarketConfig], history: dict[str, list[dict[str, Any]]], now_utc: datetime) -> Path:
    apply_dashboard_theme()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    chart_path = settings.output_dir / f"stablecoin_monitor_v3_{now_utc.astimezone(JST).strftime('%Y%m%d_%H%M%S')}.png"

    fig = plt.figure(figsize=(12, 8))
    fig.patch.set_facecolor('#07111f')
    grid = fig.add_gridspec(4, 1, height_ratios=[1.15, 1.0, 1.0, 0.9], hspace=0.22)

    ax_price = fig.add_subplot(grid[0, 0])
    ax_btc_cvd = fig.add_subplot(grid[1, 0], sharex=ax_price)
    ax_fiat_cvd = fig.add_subplot(grid[2, 0], sharex=ax_price)
    ax_cross_cvd = fig.add_subplot(grid[3, 0], sharex=ax_price)

    primary_key = pick_primary_btc_market(markets, history)
    if primary_key:
        rows = history[primary_key]
        x = [row['bucket_start_utc'].astimezone(JST) for row in rows]
        y = [float(row['close']) for row in rows]
        ax_price.plot(x, y, linewidth=1.35, color='#f5a524', label=f'BTC price ({market_label(primary_key)})')
        ax_price.legend(loc='upper left', frameon=False)
        latest_price = y[-1]
    else:
        latest_price = 0.0

    ax_price.set_title('Stablecoin Flow Monitor v3.00 - CCXT OHLCV Proxy CVD', fontweight='bold')
    ax_price.set_ylabel('BTC price')
    ax_price.grid(True, alpha=0.22)

    ax_btc_cvd.set_title('BTC / Stable Proxy CVD', loc='left', pad=4, fontweight='bold')
    plot_category_cvd(ax_btc_cvd, history, markets, 'btc_stable')

    ax_fiat_cvd.set_title('Stable / Fiat Proxy CVD', loc='left', pad=4, fontweight='bold')
    plot_category_cvd(ax_fiat_cvd, history, markets, 'stable_fiat')

    ax_cross_cvd.set_title('Stable / Stable Proxy CVD', loc='left', pad=4, fontweight='bold')
    plot_category_cvd(ax_cross_cvd, history, markets, 'stable_cross')

    locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
    formatter = mdates.DateFormatter('%m-%d %H:%M', tz=JST)
    for ax in (ax_price, ax_btc_cvd, ax_fiat_cvd, ax_cross_cvd):
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)
        ax.tick_params(axis='both', labelsize=7)
    for ax in (ax_price, ax_btc_cvd, ax_fiat_cvd):
        ax.tick_params(labelbottom=False)

    summary = build_flow_summary(settings, markets, history, latest_price)
    fig.suptitle(summary.split('\n')[0], fontsize=10, color='#dbe7ff', y=0.975)
    fig.text(0.985, 0.015, now_utc.astimezone(JST).strftime('%Y-%m-%d %H:%M JST'),
             ha='right', va='bottom', fontsize=7, color='#9fb0c9')
    fig.subplots_adjust(left=0.07, right=0.985, top=0.94, bottom=0.08)
    fig.savefig(chart_path, dpi=150)
    plt.close(fig)
    return chart_path


def category_recent_delta(
    history: dict[str, list[dict[str, Any]]],
    markets: list[MarketConfig],
    category: str,
    lookback_minutes: int = 15,
) -> float:
    cutoff = datetime.now(UTC) - timedelta(minutes=lookback_minutes)
    total = 0.0
    for market in markets:
        if market.category != category:
            continue
        for row in history.get(market.market_key, []):
            if row['bucket_start_utc'] >= cutoff:
                total += float(row['proxy_delta_quote'] or 0.0)
    return total


def build_flow_summary(settings: Settings, markets: list[MarketConfig], history: dict[str, list[dict[str, Any]]], latest_btc_price: float) -> str:
    btc_delta = category_recent_delta(history, markets, 'btc_stable')
    fiat_delta = category_recent_delta(history, markets, 'stable_fiat')
    cross_delta = category_recent_delta(history, markets, 'stable_cross')
    threshold = settings.flow_delta_threshold_quote

    if btc_delta < -threshold and fiat_delta < -threshold:
        signal = 'FIAT_EXIT'
        note = 'BTC売りとフィアット出口が同時に強い'
    elif btc_delta > threshold and fiat_delta >= 0:
        signal = 'BTC_DEMAND'
        note = 'ステーブル資金がBTCへ向かうバイアス'
    elif fiat_delta > threshold and abs(btc_delta) < threshold:
        signal = 'STABLE_INFLOW'
        note = 'フィアットからステーブル流入、BTC反応はまだ弱い'
    elif abs(cross_delta) > threshold and abs(btc_delta) < threshold:
        signal = 'STABLE_ROTATION'
        note = 'ステーブル間の乗り換え需要が優勢'
    elif btc_delta < -threshold:
        signal = 'BTC_RISK_OFF'
        note = 'BTCからステーブルへ逃避するバイアス'
    else:
        signal = 'MIXED'
        note = '方向感は混在。断定しない'

    return (
        f'BTC {human_price(latest_btc_price)} | Signal: {signal} | '
        f'BTC/stable {human_volume(btc_delta)} | stable/fiat {human_volume(fiat_delta)} | '
        f'stable/cross {human_volume(cross_delta)}\n{note}'
    )


def send_discord(webhook_url: str, content: str, chart_path: Path, timeout_seconds: int) -> None:
    with chart_path.open('rb') as fh:
        response = requests.post(
            webhook_url,
            data={'content': content},
            files={'file': (chart_path.name, fh, 'image/png')},
            timeout=timeout_seconds,
        )
    response.raise_for_status()


def fetch_and_store_snapshot(settings: Settings, markets: list[MarketConfig]) -> bool:
    try:
        bars = fetch_ohlcv_for_markets(settings, markets)
        save_ohlcv_bars(settings.db_path, bars)
        deleted = prune_db(settings.db_path, settings.retention_days)
        if deleted:
            logging.info('Pruned %s old rows.', deleted)
        return bool(bars)
    except KeyboardInterrupt:
        raise
    except Exception:
        logging.exception('Fetch/store OHLCV snapshot failed.')
        return False


def render_and_notify_chart(settings: Settings, markets: list[MarketConfig]) -> bool:
    try:
        history = load_ohlcv_history(settings.db_path, settings.history_days)
    except KeyboardInterrupt:
        raise
    except Exception:
        logging.exception('History loading failed.')
        return False

    if not history:
        logging.warning('No OHLCV history yet; render skipped.')
        return False

    try:
        ts_utc = datetime.now(UTC).replace(microsecond=0)
        chart_path = make_chart(settings, markets, history, ts_utc)
        logging.info('Chart saved: %s', chart_path)
    except KeyboardInterrupt:
        raise
    except Exception:
        logging.exception('Chart generation failed.')
        return False

    primary_key = pick_primary_btc_market(markets, history)
    latest_price = float(history[primary_key][-1]['close']) if primary_key else 0.0
    summary = build_flow_summary(settings, markets, history, latest_price)

    if settings.discord_webhook_url:
        try:
            send_discord(settings.discord_webhook_url, summary, chart_path, settings.request_timeout_seconds)
            logging.info('Discord notification sent.')
        except KeyboardInterrupt:
            raise
        except Exception:
            logging.exception('Discord notification failed.')
    else:
        logging.info('DISCORD_WEBHOOK_URL not set. Chart saved locally only.')
        print(summary)

    return True


def run_once(settings: Settings, markets: list[MarketConfig]) -> bool:
    if not fetch_and_store_snapshot(settings, markets):
        return False
    return render_and_notify_chart(settings, markets)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Stablecoin flow monitor using CCXT public OHLCV + local SQLite history.')
    parser.add_argument('--once', action='store_true', help='Run one fetch/render cycle and exit.')
    parser.add_argument('--loop', action='store_true', help='Run forever at the configured interval.')
    parser.add_argument('--log-level', default=os.getenv('LOG_LEVEL', 'INFO'), help='Logging level. Default: INFO')
    return parser.parse_args()


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format='%(asctime)s | %(levelname)s | %(message)s',
    )

    try:
        settings = load_settings(base_dir)
        markets = load_markets_config(settings.markets_config_path)
    except ConfigError as exc:
        logging.error(str(exc))
        return 2

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    init_db(settings.db_path)
    sync_market_pairs(settings.db_path, markets)

    run_forever = args.loop or not args.once
    try:
        if not run_forever:
            if not run_once(settings, markets):
                return 1
            return 0

        now = time.monotonic()
        next_fetch_at = now + settings.fetch_interval_seconds
        next_render_at = now + settings.render_interval_seconds

        logging.info(
            'Starting v3.00 CCXT OHLCV loop. Fetch=%ss Render=%ss Timeframe=%s Markets=%s',
            settings.fetch_interval_seconds,
            settings.render_interval_seconds,
            settings.ohlcv_timeframe,
            len([m for m in markets if m.enabled]),
        )
        while True:
            now = time.monotonic()

            while now >= next_fetch_at:
                next_fetch_at += settings.fetch_interval_seconds
                if fetch_and_store_snapshot(settings, markets):
                    logging.info('OHLCV fetch completed.')

            while now >= next_render_at:
                next_render_at += settings.render_interval_seconds
                if not render_and_notify_chart(settings, markets):
                    logging.warning('Render task failed.')

            time.sleep(1)
    except KeyboardInterrupt:
        logging.info('Stopped by user.')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())

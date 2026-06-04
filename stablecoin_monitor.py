from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from zoneinfo import ZoneInfo

UTC = timezone.utc
JST = ZoneInfo('Asia/Tokyo')
CMC_QUOTES_LATEST_URL = 'https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest'
DEFAULT_SYMBOLS = ('BTC', 'USDT', 'USDC', 'FDUSD')
DEFAULT_STABLES = ('USDT', 'USDC', 'FDUSD')
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_HISTORY_DAYS = 1
DEFAULT_RETENTION_DAYS = 30
DEFAULT_ALERT_BP = 15.0


@dataclass(slots=True)
class Settings:
    cmc_api_key: str
    discord_webhook_url: str | None
    db_path: Path
    output_dir: Path
    poll_interval_seconds: int
    fetch_interval_seconds: int
    render_interval_seconds: int
    history_days: int
    retention_days: int
    stable_symbols: tuple[str, ...]
    btc_symbol: str
    deviation_alert_bp: float
    request_timeout_seconds: int


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


def env_symbols(name: str, default: Iterable[str]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        return tuple(default)
    values = tuple(part.strip().upper() for part in raw.split(',') if part.strip())
    if not values:
        raise ConfigError(f'{name} must contain at least one symbol')
    return values


def load_settings(base_dir: Path) -> Settings:
    load_dotenv(base_dir / '.env')

    cmc_api_key = (os.getenv('CMC_API_KEY') or '').strip()
    if not cmc_api_key:
        raise ConfigError('CMC_API_KEY is not set. Put it in .env or your shell environment.')

    output_dir = Path(os.getenv('OUTPUT_DIR', base_dir / 'output')).expanduser().resolve()
    db_path = Path(os.getenv('DB_PATH', base_dir / 'data' / 'stablecoin_monitor.db')).expanduser().resolve()
    btc_symbol = os.getenv('BTC_SYMBOL', 'BTC').strip().upper() or 'BTC'
    stable_symbols = env_symbols('STABLE_SYMBOLS', DEFAULT_STABLES)
    if btc_symbol in stable_symbols:
        raise ConfigError('BTC_SYMBOL must not also be inside STABLE_SYMBOLS')

    # Backward compatibility: use POLL_INTERVAL_SECONDS if new intervals not set
    poll_interval = env_int('POLL_INTERVAL_SECONDS', DEFAULT_INTERVAL_SECONDS)
    fetch_interval = env_int('FETCH_INTERVAL_SECONDS', poll_interval)
    render_interval = env_int('RENDER_INTERVAL_SECONDS', poll_interval)

    return Settings(
        cmc_api_key=cmc_api_key,
        discord_webhook_url=(os.getenv('DISCORD_WEBHOOK_URL') or '').strip() or None,
        db_path=db_path,
        output_dir=output_dir,
        poll_interval_seconds=poll_interval,
        fetch_interval_seconds=fetch_interval,
        render_interval_seconds=render_interval,
        history_days=env_int('HISTORY_DAYS', DEFAULT_HISTORY_DAYS),
        retention_days=env_int('RETENTION_DAYS', DEFAULT_RETENTION_DAYS),
        stable_symbols=stable_symbols,
        btc_symbol=btc_symbol,
        deviation_alert_bp=env_float('DEVIATION_ALERT_BP', DEFAULT_ALERT_BP),
        request_timeout_seconds=env_int('REQUEST_TIMEOUT_SECONDS', 20),
    )


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({'GET', 'POST'}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session


def get_db_connection(db_path: Path, read_only: bool = False) -> sqlite3.Connection:
    """Create a SQLite connection with optimized PRAGMAs for the monitor."""
    if read_only:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn

def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_db_connection(db_path) as conn:
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
        conn.commit()


def fetch_quotes_latest(session: requests.Session, settings: Settings) -> dict[str, dict[str, Any]]:
    symbols = [settings.btc_symbol, *settings.stable_symbols]
    response = session.get(
        CMC_QUOTES_LATEST_URL,
        headers={
            'Accept': 'application/json',
            'X-CMC_PRO_API_KEY': settings.cmc_api_key,
        },
        params={
            'symbol': ','.join(symbols),
            'convert': 'USD',
            'skip_invalid': 'true',
        },
        timeout=settings.request_timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    status = payload.get('status') or {}
    if status.get('error_code') not in (None, 0):
        raise RuntimeError(f"CMC error {status.get('error_code')}: {status.get('error_message')}")

    data = payload.get('data') or {}
    normalized: dict[str, dict[str, Any]] = {}
    for requested_symbol in symbols:
        bucket = data.get(requested_symbol)
        if not bucket:
            continue
        items = bucket if isinstance(bucket, list) else [bucket]
        chosen = choose_best_quote_candidate(requested_symbol, items)
        quote = chosen.get('quote', {}).get('USD', {})
        normalized[requested_symbol] = {
            'symbol': requested_symbol,
            'name': chosen.get('name', requested_symbol),
            'price_usd': float(quote.get('price', 0.0) or 0.0),
            'volume_24h_usd': float(quote.get('volume_24h', 0.0) or 0.0),
            'market_cap_usd': float(quote.get('market_cap', 0.0) or 0.0),
            'cmc_rank': int(chosen.get('cmc_rank') or 999999),
            'last_updated': quote.get('last_updated'),
        }
    missing = [symbol for symbol in symbols if symbol not in normalized]
    if missing:
        raise RuntimeError(f'CMC response missing requested symbols: {", ".join(missing)}')
    return normalized


def choose_best_quote_candidate(symbol: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    matching = [item for item in items if str(item.get('symbol', '')).upper() == symbol.upper()]
    pool = matching or items

    def sort_key(item: dict[str, Any]) -> tuple[int, float, str]:
        rank = item.get('cmc_rank')
        rank_value = int(rank) if isinstance(rank, int) or (isinstance(rank, str) and rank.isdigit()) else 999999
        market_cap = item.get('quote', {}).get('USD', {}).get('market_cap') or 0.0
        try:
            market_cap_value = float(market_cap)
        except (TypeError, ValueError):
            market_cap_value = 0.0
        name = str(item.get('name', ''))
        return (rank_value, -market_cap_value, name)

    return sorted(pool, key=sort_key)[0]


def save_snapshot(db_path: Path, ts_utc: datetime, quotes: dict[str, dict[str, Any]]) -> None:
    rows = [
        (
            ts_utc.isoformat(),
            symbol,
            payload['price_usd'],
            payload['volume_24h_usd'],
            payload['market_cap_usd'],
            payload['cmc_rank'],
        )
        for symbol, payload in quotes.items()
    ]
    with get_db_connection(db_path) as conn:
        conn.executemany(
            '''
            INSERT OR REPLACE INTO snapshots
            (ts_utc, symbol, price_usd, volume_24h_usd, market_cap_usd, cmc_rank)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            rows,
        )
        conn.commit()


def prune_db(db_path: Path, retention_days: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    with get_db_connection(db_path, read_only=False) as conn:
        cursor = conn.execute(
            'DELETE FROM snapshots WHERE ts_utc < ?',
            (cutoff.isoformat(),),
        )
        conn.commit()
        return cursor.rowcount


def load_history(db_path: Path, days: int, symbols: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    placeholders = ','.join('?' for _ in symbols)
    params = [cutoff, *symbols]
    sql = f'''
        SELECT ts_utc, symbol, price_usd, volume_24h_usd, market_cap_usd, cmc_rank
        FROM snapshots
        WHERE ts_utc >= ?
          AND symbol IN ({placeholders})
        ORDER BY ts_utc ASC
    '''
    out: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    with get_db_connection(db_path, read_only=True) as conn:
        for ts_utc, symbol, price_usd, volume_24h_usd, market_cap_usd, cmc_rank in conn.execute(sql, params):
            out[symbol].append(
                {
                    'ts_utc': datetime.fromisoformat(ts_utc),
                    'price_usd': float(price_usd),
                    'volume_24h_usd': float(volume_24h_usd or 0.0),
                    'market_cap_usd': float(market_cap_usd or 0.0),
                    'cmc_rank': int(cmc_rank or 0),
                }
            )
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


def human_deviation_bp(value: float) -> str:
    return f'{value:+.1f}bp'


def start_weighted_baseline_bp(rows: list[dict[str, Any]], window_hours: int = 3) -> float:
    """Return a baseline from the oldest window, weighting older points more."""
    if not rows:
        return 0.0

    start_ts = rows[0]['ts_utc']
    cutoff_ts = start_ts + timedelta(hours=window_hours)
    weighted_sum = 0.0
    weight_sum = 0.0

    for row in rows:
        ts_utc = row['ts_utc']
        if ts_utc > cutoff_ts:
            break
        deviation_bp = (row['price_usd'] - 1.0) * 10000.0
        # Oldest point gets the largest weight; newest point in the window still counts.
        weight = (cutoff_ts - ts_utc).total_seconds() + 1.0
        weighted_sum += deviation_bp * weight
        weight_sum += weight

    if weight_sum == 0.0:
        return (rows[0]['price_usd'] - 1.0) * 10000.0
    return weighted_sum / weight_sum


def add_baseline_colored_line(
    ax: plt.Axes,
    x: list[datetime],
    y: list[float],
    baseline: float,
    *,
    linewidth: float = 0.9,
    zorder: int = 2,
) -> None:
    """Draw a line whose segments switch color above/below a baseline."""
    if len(x) < 2:
        if x and y:
            color = '#22c55e' if y[0] >= baseline else '#EF4444'
            ax.plot(x, y, linewidth=linewidth, color=color, zorder=zorder)
        return

    x_num = mdates.date2num(x)
    segments: list[list[tuple[float, float]]] = []
    colors: list[str] = []

    for i in range(len(y) - 1):
        x0, x1 = float(x_num[i]), float(x_num[i + 1])
        y0, y1 = y[i], y[i + 1]
        color0 = '#22c55e' if y0 >= baseline else '#EF4444'
        color1 = '#22c55e' if y1 >= baseline else '#EF4444'

        if color0 == color1 or y0 == y1:
            segments.append([(x0, y0), (x1, y1)])
            colors.append(color0)
            continue

        ratio = (baseline - y0) / (y1 - y0)
        xc = x0 + (x1 - x0) * ratio
        segments.append([(x0, y0), (xc, baseline)])
        colors.append(color0)
        segments.append([(xc, baseline), (x1, y1)])
        colors.append(color1)

    collection = LineCollection(segments, colors=colors, linewidths=linewidth, zorder=zorder)
    ax.add_collection(collection)


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
        'font.size': 11,
        'axes.titlesize': 13,
        'axes.labelsize': 11,
        'legend.fontsize': 9,
    })


def make_chart(settings: Settings, history: dict[str, list[dict[str, Any]]], now_utc: datetime) -> Path:
    apply_dashboard_theme()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    chart_path = settings.output_dir / f"stablecoin_monitor_{now_utc.astimezone(JST).strftime('%Y%m%d_%H%M%S')}.png"

    # v2.01: 3-row dashboard with Y-axis label spacing fix
    fig = plt.figure(figsize=(7, 5))
    fig.patch.set_facecolor('#07111f')
    outer = fig.add_gridspec(3, 1, height_ratios=[1.5, 0.6, 0.6], hspace=0.26)

    stable_palette = ['#4ade80', '#60a5fa', '#f59e0b']

    # Top row: BTC full-width card
    ax_btc = fig.add_subplot(outer[0, 0])
    btc_rows = history.get(settings.btc_symbol, [])
    if btc_rows:
        btc_x = [row['ts_utc'].astimezone(JST) for row in btc_rows]
        btc_y = [row['price_usd'] for row in btc_rows]
        ax_btc.plot(btc_x, btc_y, linewidth=1.45, color='#f5a524', label=f'{settings.btc_symbol} price')
        ax_btc.legend(loc='upper left', fontsize=7, frameon=False)
    ax_btc.set_title('Stablecoin Monitor', fontsize=12, fontweight='bold', pad=15)
    ax_btc.set_ylabel('BTC price', fontsize=8)
    ax_btc.grid(True, alpha=0.22)
    ax_btc.tick_params(axis='both', labelsize=7)
    ax_btc.tick_params(labelbottom=False)

    # Middle row: Deviation 1x3 cards (horizontal, not stacked, not combined)
    dev_grid = outer[1, 0].subgridspec(1, 3, wspace=0.24)
    ax_devs = [fig.add_subplot(dev_grid[0, i], sharex=ax_btc) for i in range(len(settings.stable_symbols))]

    # Shared tick control for middle row (time labels)
    dev_locator = mdates.AutoDateLocator(minticks=2, maxticks=4)
    dev_formatter = mdates.DateFormatter('%m-%d %H:%M', tz=JST)

    for ax, symbol, color in zip(ax_devs, settings.stable_symbols, stable_palette):
        rows = history.get(symbol, [])
        if rows:
            x = [row['ts_utc'].astimezone(JST) for row in rows]
            deviation_bp = [(row['price_usd'] - 1.0) * 10000.0 for row in rows]
            baseline_bp = start_weighted_baseline_bp(rows)
            # Draw colored background bands based on the oldest 3h weighted baseline
            # Green band above baseline, red band below baseline
            y_min, y_max = min(deviation_bp), max(deviation_bp)
            ax.axhspan(baseline_bp, y_max, color='#22c55e', alpha=0.07, zorder=0)
            ax.axhspan(y_min, baseline_bp, color='#EF4444', alpha=0.07, zorder=0)
            # Draw deviation line segments colored by position relative to baseline.
            # Background bands stay subtle; the line itself switches at baseline crossings.
            add_baseline_colored_line(ax, x, deviation_bp, baseline_bp, linewidth=0.9, zorder=4)
            # Draw baseline from the oldest 3h weighted average
            ax.axhline(baseline_bp, linestyle=':', linewidth=0.8, color='#dbeafe', alpha=0.85, zorder=3)
            # Set Y-axis range based on min/max values in the period, ensuring 0 line is always visible
            y_range = y_max - y_min
            if y_range == 0:
                # Handle flat line case
                ax.set_ylim(y_min - 1.0, y_max + 1.0)
            else:
                y_padding = y_range * 0.1
                lower = min(y_min - y_padding, 0)
                upper = max(y_max + y_padding, 0)
                ax.set_ylim(lower, upper)
        ax.axhline(0.0, linestyle='--', linewidth=0.7, color='#a5b8d6')
        ax.set_title(f'{symbol} Deviation', loc='left', pad=4, fontsize=7.5, fontweight='bold')
        ax.set_ylabel('')
        ax.grid(True, alpha=0.22)
        ax.tick_params(axis='both', labelsize=6)
        # Show X-axis labels on middle row cards for time reference
        ax.xaxis.set_major_locator(dev_locator)
        ax.xaxis.set_major_formatter(dev_formatter)

    # Bottom row: Volume 1x3 cards (horizontal, not stacked)
    vol_grid = outer[2, 0].subgridspec(1, 3, wspace=0.24)
    ax_vols = [fig.add_subplot(vol_grid[0, i], sharex=ax_btc) for i in range(len(settings.stable_symbols))]

    # Compact tick control for narrow cards. Avoid ConciseDateFormatter offset text
    # because it collides with the compact 7x5 dashboard footer.
    locator = mdates.AutoDateLocator(minticks=2, maxticks=4)
    formatter = mdates.DateFormatter('%m-%d %H:%M', tz=JST)

    for i, (ax, symbol, color) in enumerate(zip(ax_vols, settings.stable_symbols, stable_palette)):
        rows = history.get(symbol, [])
        if rows:
            x = [row['ts_utc'].astimezone(JST) for row in rows]
            y = [row['volume_24h_usd'] / 1_000_000_000 for row in rows]
            ax.plot(x, y, linewidth=1.05, color=color)
        ax.set_title(f'{symbol} 24h volume', loc='left', pad=3, fontsize=7.5, fontweight='bold')
        ax.set_ylabel('')
        ax.grid(True, alpha=0.22)
        ax.tick_params(axis='both', labelsize=6)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)

    latest_labels = []
    for symbol in settings.stable_symbols:
        rows = history.get(symbol, [])
        if rows:
            latest_labels.append(f"{symbol} {(rows[-1]['price_usd'] - 1.0) * 10000.0:+.1f}bp")
    if btc_rows:
        latest_labels.insert(0, f"{settings.btc_symbol} ${btc_rows[-1]['price_usd']:,.0f}")
    if latest_labels:
        fig.suptitle(' | '.join(latest_labels), fontsize=10, color='#dbe7ff', y=0.96)
    fig.text(0.985, 0.018, now_utc.astimezone(JST).strftime('%Y-%m-%d %H:%M JST'),
             ha='right', va='bottom', fontsize=6.5, color='#9fb0c9')

    fig.subplots_adjust(left=0.075, right=0.985, top=0.93, bottom=0.095)
    fig.savefig(chart_path, dpi=150)
    plt.close(fig)
    return chart_path


def pct_change(current: float, previous: float | None) -> float | None:
    if previous in (None, 0):
        return None
    return ((current / previous) - 1.0) * 100.0


def build_summary(settings: Settings, quotes: dict[str, dict[str, Any]], history: dict[str, list[dict[str, Any]]]) -> str:
    lines: list[str] = []
    btc_now = quotes[settings.btc_symbol]['price_usd']
    btc_prev = history[settings.btc_symbol][-2]['price_usd'] if len(history[settings.btc_symbol]) >= 2 else None
    btc_chg = pct_change(btc_now, btc_prev)
    if btc_chg is None:
        lines.append(f"BTC ${btc_now:,.2f}")
    else:
        lines.append(f"BTC ${btc_now:,.2f} ({btc_chg:+.3f}% vs prev)")

    alerts: list[str] = []
    stable_parts: list[str] = []
    for symbol in settings.stable_symbols:
        price = quotes[symbol]['price_usd']
        deviation_bp = (price - 1.0) * 10000.0
        volume_b = quotes[symbol]['volume_24h_usd'] / 1_000_000_000
        stable_parts.append(f"{symbol} ${price:.6f} ({deviation_bp:+.1f}bp, vol ${volume_b:.2f}B)")
        if abs(deviation_bp) >= settings.deviation_alert_bp:
            alerts.append(f"{symbol} {deviation_bp:+.1f}bp")
    lines.extend(stable_parts)
    if alerts:
        lines.append('ALERT: ' + ', '.join(alerts))
    return '\n'.join(lines)


def send_discord(webhook_url: str, content: str, chart_path: Path, timeout_seconds: int) -> None:
    with chart_path.open('rb') as fh:
        response = requests.post(
            webhook_url,
            data={'content': content},
            files={'file': (chart_path.name, fh, 'image/png')},
            timeout=timeout_seconds,
        )
    response.raise_for_status()


def build_latest_quotes_from_history(settings: Settings, history: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    quotes: dict[str, dict[str, Any]] = {}
    for symbol in (settings.btc_symbol, *settings.stable_symbols):
        rows = history.get(symbol, [])
        if rows:
            latest = rows[-1]
            quotes[symbol] = {
                'price_usd': latest['price_usd'],
                'volume_24h_usd': latest['volume_24h_usd'],
            }
        elif symbol == settings.btc_symbol:
            quotes[symbol] = {'price_usd': 0.0, 'volume_24h_usd': 0.0}
        else:
            quotes[symbol] = {'price_usd': 1.0, 'volume_24h_usd': 0.0}
    return quotes


def fetch_and_store_snapshot(session: requests.Session, settings: Settings) -> bool:
    """Fetch the latest quotes and persist them to SQLite."""
    try:
        ts_utc = datetime.now(UTC).replace(microsecond=0)
        quotes = fetch_quotes_latest(session, settings)
        save_snapshot(settings.db_path, ts_utc, quotes)
        deleted = prune_db(settings.db_path, settings.retention_days)
        if deleted:
            logging.info('Pruned %s old rows.', deleted)
        return True
    except KeyboardInterrupt:
        raise
    except Exception:
        logging.exception('Fetch/store snapshot failed.')
        return False


def render_and_notify_chart(settings: Settings) -> bool:
    """Load history, render the chart, and notify Discord if configured."""
    try:
        symbols = (settings.btc_symbol, *settings.stable_symbols)
        history = load_history(settings.db_path, settings.history_days, symbols)
    except KeyboardInterrupt:
        raise
    except Exception:
        logging.exception('History loading failed.')
        return False

    try:
        ts_utc = datetime.now(UTC).replace(microsecond=0)
        chart_path = make_chart(settings, history, ts_utc)
        logging.info('Chart saved: %s', chart_path)
    except KeyboardInterrupt:
        raise
    except Exception:
        logging.exception('Chart generation failed.')
        return False

    try:
        latest_quotes = build_latest_quotes_from_history(settings, history)
        summary = build_summary(settings, latest_quotes, history)
    except KeyboardInterrupt:
        raise
    except Exception:
        logging.warning('Summary generation failed; continuing without Discord summary.', exc_info=True)
        summary = 'Stablecoin monitor chart updated.'

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

    return True


def run_once(settings: Settings, session: requests.Session) -> bool:
    if not fetch_and_store_snapshot(session, settings):
        return False
    return render_and_notify_chart(settings)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Stablecoin monitor using CoinMarketCap + local SQLite history.')
    parser.add_argument('--once', action='store_true', help='Run one cycle and exit.')
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
    except ConfigError as exc:
        logging.error(str(exc))
        return 2

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    init_db(settings.db_path)
    session = build_session()

    run_forever = args.loop or not args.once
    try:
        if not run_forever:
            if not run_once(settings, session):
                return 1
            return 0

        # Initialize schedule boundaries
        now = time.monotonic()
        next_fetch_at = now + settings.fetch_interval_seconds
        next_render_at = now + settings.render_interval_seconds

        logging.info('Starting dual-interval loop. Fetch=%ss, Render=%ss',
                     settings.fetch_interval_seconds, settings.render_interval_seconds)
        while True:
            now = time.monotonic()

            # Process all due fetch cycles
            while now >= next_fetch_at:
                next_fetch_at += settings.fetch_interval_seconds
                try:
                    if fetch_and_store_snapshot(session, settings):
                        logging.info('Fetch completed.')
                except KeyboardInterrupt:
                    raise

            # Process all due render cycles
            while now >= next_render_at:
                next_render_at += settings.render_interval_seconds
                try:
                    if not render_and_notify_chart(settings):
                        logging.warning('Render task failed.')
                except KeyboardInterrupt:
                    raise

            # Sleep for 1 second to avoid busy loop
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info('Stopped by user.')
        return 0
    finally:
        session.close()


if __name__ == '__main__':
    raise SystemExit(main())

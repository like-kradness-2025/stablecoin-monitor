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
DEFAULT_HISTORY_DAYS = 3
DEFAULT_RETENTION_DAYS = 30
DEFAULT_ALERT_BP = 15.0


@dataclass(slots=True)
class Settings:
    cmc_api_key: str
    discord_webhook_url: str | None
    db_path: Path
    output_dir: Path
    poll_interval_seconds: int
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

    return Settings(
        cmc_api_key=cmc_api_key,
        discord_webhook_url=(os.getenv('DISCORD_WEBHOOK_URL') or '').strip() or None,
        db_path=db_path,
        output_dir=output_dir,
        poll_interval_seconds=env_int('POLL_INTERVAL_SECONDS', DEFAULT_INTERVAL_SECONDS),
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


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
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
    with sqlite3.connect(db_path) as conn:
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
    with sqlite3.connect(db_path) as conn:
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
    with sqlite3.connect(db_path) as conn:
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


def apply_dashboard_theme() -> None:
    plt.style.use('dark_background')
    plt.rcParams.update({
        'figure.facecolor': '#050816',
        'axes.facecolor': '#0a1020',
        'axes.edgecolor': '#20304a',
        'axes.labelcolor': '#dbe7ff',
        'xtick.color': '#9fb0c9',
        'ytick.color': '#9fb0c9',
        'text.color': '#eef4ff',
        'axes.titlecolor': '#f5f8ff',
        'grid.color': '#1f2f4d',
        'grid.alpha': 0.18,
        'font.size': 10.5,
        'axes.titlesize': 12.5,
        'axes.labelsize': 10.5,
        'legend.fontsize': 8.8,
    })


def make_chart(settings: Settings, history: dict[str, list[dict[str, Any]]], now_utc: datetime) -> Path:
    apply_dashboard_theme()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    chart_path = settings.output_dir / f"stablecoin_monitor_{now_utc.astimezone(JST).strftime('%Y%m%d_%H%M%S')}.png"

    fig, axes = plt.subplots(3, 1, figsize=(16, 10), sharex=True)
    fig.patch.set_facecolor('#050816')
    fig.subplots_adjust(left=0.06, right=0.985, top=0.86, bottom=0.08, hspace=0.18)

    accent = fig.add_axes([0.06, 0.965, 0.93, 0.006])
    accent.set_facecolor('#050816')
    accent.set_xticks([])
    accent.set_yticks([])
    for spine in accent.spines.values():
        spine.set_visible(False)
    accent.axhline(0.5, color='#ffb020', linewidth=5.5, alpha=0.92, solid_capstyle='round')
    accent.axhline(0.5, color='#2ee59d', linewidth=2.1, alpha=0.88, solid_capstyle='round')

    stable_palette = ['#2ee59d', '#67d8ff', '#f97316', '#d66bff', '#25e6ff', '#fb7185', '#8b5cf6']
    palette = {settings.btc_symbol: '#ffb020'}
    for symbol, color in zip(settings.stable_symbols, stable_palette):
        palette[symbol] = color

    card_bg = '#0a1020'
    border = '#223657'
    for ax in axes:
        ax.set_facecolor(card_bg)
        for spine in ax.spines.values():
            spine.set_color(border)
            spine.set_linewidth(0.8)
        ax.grid(True, alpha=0.16)

    btc_rows = history[settings.btc_symbol]
    btc_x = [row['ts_utc'].astimezone(JST) for row in btc_rows]
    btc_y = [row['price_usd'] for row in btc_rows]
    btc_color = palette.get(settings.btc_symbol, '#ffb020')
    axes[0].plot(btc_x, btc_y, linewidth=2.6, color=btc_color)
    axes[0].fill_between(btc_x, btc_y, color=btc_color, alpha=0.08)
    axes[0].set_title(f'{settings.btc_symbol} price', loc='left', pad=10, fontweight='bold')
    axes[0].set_ylabel(f'{settings.btc_symbol} / USD')
    if btc_y:
        axes[0].annotate(
            human_price(btc_y[-1]),
            xy=(btc_x[-1], btc_y[-1]),
            xytext=(8, 0),
            textcoords='offset points',
            va='center',
            color='#f8fafc',
            bbox=dict(boxstyle='round,pad=0.25', facecolor='#111a2d', edgecolor=btc_color, linewidth=1.0),
        )

    latest_snapshot_rows: list[tuple[str, float, float, float]] = []
    stable_lines = []
    deviations_now: list[float] = []
    volumes_now: list[float] = []
    for symbol in settings.stable_symbols:
        rows = history[symbol]
        if not rows:
            continue
        color = palette.get(symbol, '#60a5fa')
        x = [row['ts_utc'].astimezone(JST) for row in rows]
        deviation_bp = [(row['price_usd'] - 1.0) * 10000.0 for row in rows]
        axes[1].plot(x, deviation_bp, linewidth=2.1, color=color, label=symbol)
        latest = rows[-1]
        latest_dev = (latest['price_usd'] - 1.0) * 10000.0
        deviations_now.append(latest_dev)
        volumes_now.append(latest['volume_24h_usd'])
        stable_lines.append(f'{symbol} {human_deviation_bp(latest_dev)}')
        latest_snapshot_rows.append((symbol, latest['price_usd'], latest_dev, latest['volume_24h_usd']))

    alert_bp = settings.deviation_alert_bp
    axes[1].axhspan(-alert_bp, alert_bp, color='#22c55e', alpha=0.05, zorder=0)
    axes[1].axhline(0.0, linestyle='--', linewidth=1.0, color='#7486a9')
    axes[1].axhline(alert_bp, linestyle=':', linewidth=0.9, color='#ef4444', alpha=0.65)
    axes[1].axhline(-alert_bp, linestyle=':', linewidth=0.9, color='#ef4444', alpha=0.65)
    axes[1].set_title(f'peg deviation', loc='left', pad=10, fontweight='bold')
    axes[1].set_ylabel('Deviation (bp)')
    axes[1].legend(loc='upper left', ncol=max(1, len(settings.stable_symbols)), frameon=False)

    axes[2].set_title('24h volume', loc='left', pad=10, fontweight='bold')
    axes[2].set_ylabel('USD bn')
    axes[2].set_xlabel('Time (JST)')
    for symbol in settings.stable_symbols:
        rows = history[symbol]
        if not rows:
            continue
        color = palette.get(symbol, '#60a5fa')
        x = [row['ts_utc'].astimezone(JST) for row in rows]
        y = [row['volume_24h_usd'] / 1_000_000_000 for row in rows]
        axes[2].plot(x, y, linewidth=2.0, color=color, label=symbol)
        axes[2].fill_between(x, y, color=color, alpha=0.05)
    axes[2].legend(loc='upper left', ncol=max(1, len(settings.stable_symbols)), frameon=False)

    locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
    formatter = mdates.ConciseDateFormatter(locator)
    axes[2].xaxis.set_major_locator(locator)
    axes[2].xaxis.set_major_formatter(formatter)
    axes[2].tick_params(axis='x', rotation=0)

    latest_btc = btc_y[-1] if btc_y else 0.0
    latest_timestamp = btc_rows[-1]['ts_utc'].astimezone(JST).strftime('%Y-%m-%d %H:%M JST') if btc_rows else now_utc.astimezone(JST).strftime('%Y-%m-%d %H:%M JST')
    headline = f'{latest_timestamp}   |   {settings.btc_symbol} {human_price(latest_btc)}'
    if stable_lines:
        headline += '   |   ' + '   '.join(stable_lines)

    fig.text(0.06, 0.952, 'Stablecoin Monitor', fontsize=19, fontweight='bold', ha='left', va='center', color='#f8fafc')
    fig.text(0.06, 0.924, headline, fontsize=10.1, ha='left', va='center', color='#aebfd7')

    # Metric cards
    card_specs = []
    if btc_y:
        prev_btc = btc_rows[-2]['price_usd'] if len(btc_rows) >= 2 else None
        btc_chg = pct_change(btc_y[-1], prev_btc)
        card_specs.append(('BTC', human_price(btc_y[-1]), f'{btc_chg:+.2f}%' if btc_chg is not None else 'n/a', btc_color))
    if deviations_now:
        avg_dev = sum(deviations_now) / len(deviations_now)
        worst_dev = max(deviations_now, key=lambda v: abs(v))
        card_specs.append(('avg peg drift', human_deviation_bp(avg_dev), 'blend of stables', '#67d8ff'))
        card_specs.append(('worst peg drift', human_deviation_bp(worst_dev), 'highest absolute move', '#fb7185'))
    if volumes_now:
        total_vol = sum(volumes_now)
        card_specs.append(('24h volume', human_volume(total_vol), f'{len(volumes_now)} stables', '#2ee59d'))

    card_specs = card_specs[:4]
    card_w = 0.22
    gap = 0.012
    x0 = 0.06
    y0 = 0.86
    h = 0.055
    for idx, (label, value, sub, color) in enumerate(card_specs):
        axc = fig.add_axes([x0 + idx * (card_w + gap), y0, card_w, h])
        axc.set_facecolor('#0a1020')
        for spine in axc.spines.values():
            spine.set_color(color)
            spine.set_linewidth(1.0)
        axc.set_xticks([])
        axc.set_yticks([])
        axc.text(0.06, 0.68, label, transform=axc.transAxes, fontsize=8.7, color='#8ea1bf', va='center', ha='left')
        axc.text(0.06, 0.32, value, transform=axc.transAxes, fontsize=15, fontweight='bold', color='#f8fafc', va='center', ha='left')
        axc.text(0.94, 0.30, sub, transform=axc.transAxes, fontsize=8.2, color=color, va='center', ha='right')

    if latest_snapshot_rows:
        status_line = '   '.join(f'{s} {human_deviation_bp(dev)}' for s, _, dev, _ in latest_snapshot_rows)
        fig.text(0.94, 0.924, status_line, fontsize=8.4, ha='right', va='center', color='#9fb0c9')

    fig.savefig(chart_path, dpi=150, facecolor=fig.get_facecolor())
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


def run_once(settings: Settings, session: requests.Session) -> tuple[Path, str]:
    ts_utc = datetime.now(UTC).replace(microsecond=0)
    quotes = fetch_quotes_latest(session, settings)
    save_snapshot(settings.db_path, ts_utc, quotes)
    deleted = prune_db(settings.db_path, settings.retention_days)
    if deleted:
        logging.info('Pruned %s old rows.', deleted)

    symbols = (settings.btc_symbol, *settings.stable_symbols)
    history = load_history(settings.db_path, settings.history_days, symbols)
    chart_path = make_chart(settings, history, ts_utc)
    summary = build_summary(settings, quotes, history)

    if settings.discord_webhook_url:
        send_discord(settings.discord_webhook_url, summary, chart_path, settings.request_timeout_seconds)
        logging.info('Discord notification sent.')
    else:
        logging.info('DISCORD_WEBHOOK_URL not set. Chart saved locally only.')

    return chart_path, summary


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
            chart_path, summary = run_once(settings, session)
            logging.info('Chart saved: %s', chart_path)
            print(summary)
            return 0

        logging.info('Starting loop. Interval=%ss', settings.poll_interval_seconds)
        while True:
            start = time.monotonic()
            try:
                chart_path, summary = run_once(settings, session)
                logging.info('Chart saved: %s', chart_path)
                print(summary)
            except KeyboardInterrupt:
                raise
            except Exception:
                logging.exception('Cycle failed.')

            elapsed = time.monotonic() - start
            sleep_seconds = max(1, settings.poll_interval_seconds - int(elapsed))
            logging.info('Sleeping %ss until next cycle.', sleep_seconds)
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        logging.info('Stopped by user.')
        return 0
    finally:
        session.close()


if __name__ == '__main__':
    raise SystemExit(main())

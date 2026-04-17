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


def make_chart(settings: Settings, history: dict[str, list[dict[str, Any]]], now_utc: datetime) -> Path:
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    chart_path = settings.output_dir / f"stablecoin_monitor_{now_utc.astimezone(JST).strftime('%Y%m%d_%H%M%S')}.png"

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True, constrained_layout=True)

    btc_rows = history[settings.btc_symbol]
    btc_x = [row['ts_utc'].astimezone(JST) for row in btc_rows]
    btc_y = [row['price_usd'] for row in btc_rows]
    axes[0].plot(btc_x, btc_y, linewidth=1.6, label=f'{settings.btc_symbol} price')
    axes[0].set_title('Stablecoin Monitor')
    axes[0].set_ylabel('BTC price (USD)')
    axes[0].grid(True, alpha=0.25)
    if btc_y:
        axes[0].legend(loc='upper left')

    for symbol in settings.stable_symbols:
        rows = history[symbol]
        x = [row['ts_utc'].astimezone(JST) for row in rows]
        deviation_bp = [(row['price_usd'] - 1.0) * 10000.0 for row in rows]
        axes[1].plot(x, deviation_bp, linewidth=1.2, label=symbol)
    axes[1].axhline(0.0, linestyle='--', linewidth=1.0)
    axes[1].set_ylabel('Deviation from $1 (bp)')
    axes[1].grid(True, alpha=0.25)
    axes[1].legend(loc='upper left', ncol=max(1, len(settings.stable_symbols)))

    for symbol in settings.stable_symbols:
        rows = history[symbol]
        x = [row['ts_utc'].astimezone(JST) for row in rows]
        y = [row['volume_24h_usd'] / 1_000_000_000 for row in rows]
        axes[2].plot(x, y, linewidth=1.1, label=symbol)
    axes[2].set_ylabel('24h volume (USD bn)')
    axes[2].set_xlabel('Time (JST)')
    axes[2].grid(True, alpha=0.25)
    axes[2].legend(loc='upper left', ncol=max(1, len(settings.stable_symbols)))

    locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
    formatter = mdates.ConciseDateFormatter(locator)
    axes[2].xaxis.set_major_locator(locator)
    axes[2].xaxis.set_major_formatter(formatter)

    latest_labels = []
    for symbol in settings.stable_symbols:
        rows = history[symbol]
        if rows:
            latest_labels.append(f"{symbol} {(rows[-1]['price_usd'] - 1.0) * 10000.0:+.1f}bp")
    if btc_rows:
        latest_labels.insert(0, f"{settings.btc_symbol} ${btc_rows[-1]['price_usd']:,.0f}")
    fig.suptitle(' | '.join(latest_labels), fontsize=12)
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

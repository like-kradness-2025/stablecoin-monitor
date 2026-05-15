from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .constants import UTC
from .models import MarketConfig, OhlcvBar


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
        # Legacy v2.x compatibility table.
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
    cutoff_iso = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
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

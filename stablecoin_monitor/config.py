from __future__ import annotations

import os
from pathlib import Path

from .constants import (
    DEFAULT_FETCH_INTERVAL_SECONDS,
    DEFAULT_FLOW_DELTA_THRESHOLD,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_OHLCV_LIMIT,
    DEFAULT_OHLCV_TIMEFRAME,
    DEFAULT_RENDER_INTERVAL_SECONDS,
    DEFAULT_RETENTION_DAYS,
)
from .exceptions import ConfigError
from .models import Settings


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


def resolve_path(base_dir: Path, raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base_dir / path).resolve()


def load_settings(base_dir: Path) -> Settings:
    load_dotenv(base_dir / '.env')

    output_dir = resolve_path(base_dir, os.getenv('OUTPUT_DIR', 'output'))
    db_path = resolve_path(base_dir, os.getenv('DB_PATH', 'data/stablecoin_monitor.db'))

    poll_interval = env_int('POLL_INTERVAL_SECONDS', DEFAULT_INTERVAL_SECONDS)
    fetch_interval = env_int(
        'FETCH_INTERVAL_SECONDS',
        env_int('MARKET_FETCH_INTERVAL_SECONDS', DEFAULT_FETCH_INTERVAL_SECONDS),
    )
    render_interval = env_int('RENDER_INTERVAL_SECONDS', DEFAULT_RENDER_INTERVAL_SECONDS)

    markets_config_path = resolve_path(
        base_dir,
        os.getenv('MARKETS_CONFIG_PATH', 'config/markets.yaml'),
    )

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

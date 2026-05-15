from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path

from stablecoin_monitor.app import StablecoinMonitorApp
from stablecoin_monitor.config import load_settings
from stablecoin_monitor.db import init_db, sync_market_pairs
from stablecoin_monitor.exceptions import ConfigError
from stablecoin_monitor.market_registry import load_markets_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Stablecoin flow monitor using CCXT public OHLCV + local SQLite history.'
    )
    parser.add_argument('--once', action='store_true', help='Run one fetch/render cycle and exit.')
    parser.add_argument('--loop', action='store_true', help='Run forever at the configured interval.')
    parser.add_argument('--log-level', default=os.getenv('LOG_LEVEL', 'INFO'), help='Logging level. Default: INFO')
    return parser.parse_args()


def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(log_level).upper(), logging.INFO),
        format='%(asctime)s | %(levelname)s | %(message)s',
    )


def bootstrap(base_dir: Path) -> StablecoinMonitorApp:
    settings = load_settings(base_dir)
    markets = load_markets_config(settings.markets_config_path)

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    init_db(settings.db_path)
    sync_market_pairs(settings.db_path, markets)

    return StablecoinMonitorApp(settings=settings, markets=markets)


def run_loop(app: StablecoinMonitorApp) -> int:
    settings = app.settings
    now = time.monotonic()
    next_fetch_at = now + settings.fetch_interval_seconds
    next_render_at = now + settings.render_interval_seconds

    logging.info(
        'Starting v3.00 CCXT OHLCV loop. Fetch=%ss Render=%ss Timeframe=%s Markets=%s',
        settings.fetch_interval_seconds,
        settings.render_interval_seconds,
        settings.ohlcv_timeframe,
        len([m for m in app.markets if m.enabled]),
    )

    try:
        while True:
            now = time.monotonic()

            while now >= next_fetch_at:
                next_fetch_at += settings.fetch_interval_seconds
                if app.fetch_and_store_snapshot():
                    logging.info('OHLCV fetch completed.')

            while now >= next_render_at:
                next_render_at += settings.render_interval_seconds
                if not app.render_and_notify_chart():
                    logging.warning('Render task failed.')

            time.sleep(1)
    except KeyboardInterrupt:
        logging.info('Stopped by user.')
        return 0


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    args = parse_args()
    configure_logging(args.log_level)

    try:
        app = bootstrap(base_dir)
    except ConfigError as exc:
        logging.error(str(exc))
        return 2

    run_forever = args.loop or not args.once
    if not run_forever:
        return 0 if app.run_once() else 1

    return run_loop(app)


if __name__ == '__main__':
    raise SystemExit(main())

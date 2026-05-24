from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import cast

from .calc import compute_cvd_features
from .config import load_settings
from .db import get_db_connection, init_db, load_cvd_inspection_summary
from .exceptions import ConfigError
from .market_registry import load_markets_config, resolve_markets_by_symbol
from .models import RunOnceResult
from .notifier import send_discord

from .receiver import CoinalyzeOhlcvReceiver


def resolve_data_db_path(settings_db_path: Path, base_dir: Path) -> Path:
    return settings_db_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='cvd_monitor', description='CVD monitor receiver utilities')
    sub = parser.add_subparsers(dest='command', required=True)

    def add_universe_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument('--universe', default=None, help='Universe config path, default: config/universe.generated.yml')

    recv = sub.add_parser('receive', help='Fetch and store raw OHLCV data')
    add_universe_arg(recv)
    recv.add_argument('--once', action='store_true', help='Run one receiver pass and exit')
    recv.add_argument('--interval', default='5min', help='Coinalyze interval, default: 5min')
    recv.add_argument('--lookback-hours', type=int, default=6, help='Refetch this many hours on each symbol')
    recv.add_argument('--symbols', default=None, help='Comma-separated Coinalyze symbols to process')
    recv.add_argument('--limit', type=int, default=None, help='Cap number of markets processed')
    recv.add_argument('--log-level', default=os.getenv('LOG_LEVEL', 'INFO'))

    run_once = sub.add_parser('run-once', help='Run receive, compute, and render once')
    add_universe_arg(run_once)
    run_once.add_argument('--interval', default='5min', help='Coinalyze interval, default: 5min')
    run_once.add_argument('--lookback-hours', type=int, default=6, help='Refetch this many hours on each symbol')
    run_once.add_argument('--symbols', default=None, help='Comma-separated Coinalyze symbols to process')
    run_once.add_argument('--limit', type=int, default=None, help='Cap number of markets processed')
    run_once.add_argument('--output', required=True, help='PNG output path')
    run_once.add_argument('--discord', action='store_true', help='Enable Discord notification if configured')
    run_once.add_argument('--dry-run', action='store_true', help='Skip receive step, use existing DB data')
    run_once.add_argument('--rolling-hours', type=int, default=24, help='Rolling window hours for CVD (default: 24, 0=unlimited)')
    run_once.add_argument('--log-level', default=os.getenv('LOG_LEVEL', 'INFO'))

    comp = sub.add_parser('compute', help='Compute derived CVD feature rows')
    add_universe_arg(comp)
    comp.add_argument('--interval', required=True, help='Source raw interval to compute from')
    comp.add_argument('--symbols', default=None, help='Comma-separated Coinalyze symbols to process')
    comp.add_argument('--limit', type=int, default=None, help='Cap number of markets processed')
    comp.add_argument('--rolling-hours', type=int, default=24, help='Rolling window hours for CVD (default: 24, 0=unlimited)')
    comp.add_argument('--log-level', default=os.getenv('LOG_LEVEL', 'INFO'))

    insp = sub.add_parser('inspect-features', help='Print CVD feature table summary')
    add_universe_arg(insp)
    insp.add_argument('--interval', default=None, help='Optional raw/feature interval filter')
    insp.add_argument('--log-level', default=os.getenv('LOG_LEVEL', 'INFO'))

    rend = sub.add_parser('render', help='Render a static CVD dashboard PNG')
    add_universe_arg(rend)
    rend.add_argument('--interval', required=True, help='Feature interval to render, e.g. 5min')
    rend.add_argument('--window-hours', required=True, type=int, help='Amount of history to include')
    rend.add_argument('--output', required=True, help='PNG output path')
    rend.add_argument('--symbols', default=None, help='Comma-separated Coinalyze symbols to render')
    rend.add_argument('--limit', type=int, default=None, help='Cap number of resolved markets')
    rend.add_argument('--log-level', default=os.getenv('LOG_LEVEL', 'INFO'))

    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format='%(asctime)s | %(levelname)s | %(message)s')


def run_once_pipeline(base_dir: Path, universe_path: Path | None, interval: str, lookback_hours: int, symbols: list[str] | None, limit: int | None, output: Path, discord: bool, dry_run: bool = False, rolling_hours: int = 24) -> RunOnceResult:
    settings = load_settings(base_dir, universe_path)
    db_path = resolve_data_db_path(settings.db_path, base_dir)
    init_db(db_path)
    markets = load_markets_config(settings.markets_config_path)
    markets = [m for m in markets if m.enabled and m.coinalyze_symbol]
    if symbols:
        markets = resolve_markets_by_symbol(markets, symbols)
    if limit is not None:
        markets = markets[:limit]
    if not markets:
        raise ValueError('No symbols remain after filtering')

    market_keys = [m.market_key for m in markets]
    if dry_run:
        received_ok = True
        received_rows = 0
        logging.info('dry-run mode: skipping receive step')
    else:
        receiver = CoinalyzeOhlcvReceiver(settings, markets)
        with get_db_connection(db_path, read_only=True) as conn:
            before_row = conn.execute(
                f"SELECT COUNT(*) FROM ohlcv_raw WHERE interval = ? AND market_key IN ({','.join(['?'] * len(market_keys))})",
                [interval, *market_keys],
            ).fetchone()
            before_rows = cast(int, before_row[0]) if before_row is not None else 0
        received_ok = receiver.run(interval=interval, lookback_hours=lookback_hours, once=True)
        with get_db_connection(db_path, read_only=True) as conn:
            after_row = conn.execute(
                f"SELECT COUNT(*) FROM ohlcv_raw WHERE interval = ? AND market_key IN ({','.join(['?'] * len(market_keys))})",
                [interval, *market_keys],
            ).fetchone()
            after_rows = cast(int, after_row[0]) if after_row is not None else 0
        received_rows = max(0, after_rows - before_rows)
    compute_stats = compute_cvd_features(db_path, interval, market_keys=market_keys, limit=None, rolling_hours=rolling_hours)
    from .renderer import render_cvd_chart
    render_result = render_cvd_chart(
        db_path=db_path,
        universe_config_path=settings.markets_config_path,
        interval=interval,
        window_hours=lookback_hours,
        output=output,
        symbols=','.join(symbols) if symbols else None,
        limit=limit,
    )
    rendered_path = str(render_result.output_path)
    selected_markets_count = render_result.selected_markets_count
    markets_with_feature_rows_count = render_result.markets_with_feature_rows_count
    plotted_series_count = render_result.plotted_series_count
    omitted_for_crowding_count = render_result.omitted_for_crowding_count
    skipped_no_feature_rows_count = render_result.skipped_no_feature_rows_count
    skipped_no_usable_cvd_values_count = render_result.skipped_no_usable_cvd_values_count
    unresolved_symbols_count = render_result.unresolved_symbols_count
    computed_rows = cast(int, compute_stats['rows_written'])
    panel_plot_map_json = json.dumps(render_result.panel_plot_map, sort_keys=True, separators=(',', ':'))
    panel_omitted_map_json = json.dumps(render_result.panel_omitted_map, sort_keys=True, separators=(',', ':'))
    summary = f"selected={selected_markets_count} with_features={markets_with_feature_rows_count} plotted={plotted_series_count} omitted_crowding={omitted_for_crowding_count} skipped_no_features={skipped_no_feature_rows_count} skipped_no_cvd={skipped_no_usable_cvd_values_count} unresolved={unresolved_symbols_count} btc_price_market={render_result.btc_price_market} panel_plot_map={panel_plot_map_json} panel_omitted_map={panel_omitted_map_json} received={received_rows} computed={computed_rows} rendered={rendered_path}"
    discord_sent = False
    discord_error: str | None = None
    if discord:
        try:
            discord_sent = send_discord(settings.discord_webhook_url, summary, render_result.output_path, settings.request_timeout_seconds)
        except Exception as exc:
            discord_error = str(exc)
            logging.warning('Discord notification failed; continuing without notification.')
    success = bool(received_ok and render_result.output_path.exists())
    failed_symbols: list[str] = [] if received_ok else [m.coinalyze_symbol for m in markets]
    return RunOnceResult(success, received_rows, computed_rows, selected_markets_count, markets_with_feature_rows_count, plotted_series_count, omitted_for_crowding_count, skipped_no_feature_rows_count, skipped_no_usable_cvd_values_count, unresolved_symbols_count, failed_symbols, rendered_path, discord, discord_sent, discord_error, summary)


def main() -> int:
    base_dir = Path(__file__).resolve().parents[1]
    args = parse_args()
    configure_logging(args.log_level)

    try:
        if args.command == 'inspect-db':
            settings = load_settings(base_dir)
            db_path = resolve_data_db_path(settings.db_path, base_dir)
            init_db(db_path)
            with get_db_connection(db_path, read_only=True) as conn:
                total = conn.execute('SELECT COUNT(*) FROM ohlcv_raw').fetchone()[0]
                print(f'ohlcv_raw rows: {total}')
            return 0
        if args.command == 'inspect-features':
            settings = load_settings(base_dir, args.universe)
            db_path = resolve_data_db_path(settings.db_path, base_dir)
            init_db(db_path)
            summary = load_cvd_inspection_summary(db_path, settings.markets_config_path, interval=args.interval)
            print('Market key | Coinalyze symbol | Symbol on exchange | Display pair | Raw rows | Eligible rows | Feature rows | Skipped rows')
            per_market = cast(list[dict[str, object]], summary['per_market'])
            for row in per_market:
                print(
                    f"{row['market_key']} | {row['coinalyze_symbol']} | {row['symbol_on_exchange']} | {row['display_pair']} | "
                    f"{row['raw_rows']} | {row['eligible_rows']} | {row['feature_rows']} | {row['skipped_rows']}"
                )
            return 0
        if args.command == 'compute':
            settings = load_settings(base_dir, args.universe)
            db_path = resolve_data_db_path(settings.db_path, base_dir)
            init_db(db_path)
            symbols = args.symbols.split(',') if args.symbols else None
            market_keys = None
            if symbols:
                settings_markets = load_markets_config(settings.markets_config_path)
                selected = resolve_markets_by_symbol([m for m in settings_markets if m.enabled and m.coinalyze_symbol], symbols)
                market_keys = [m.market_key for m in selected]
            stats = compute_cvd_features(db_path, args.interval, market_keys=market_keys, limit=args.limit, rolling_hours=args.rolling_hours)
            print(f'rows_read={stats["rows_read"]} rows_skipped={stats["rows_skipped"]} rows_written={stats["rows_written"]} symbols_processed={stats["symbols_processed"]}')
            return 0

        if args.command == 'render':
            settings = load_settings(base_dir, args.universe)
            db_path = resolve_data_db_path(settings.db_path, base_dir)
            init_db(db_path)
            from .renderer import render_cvd_chart
            result = render_cvd_chart(
                db_path=db_path,
                universe_config_path=settings.markets_config_path,
                interval=args.interval,
                window_hours=args.window_hours,
                output=Path(args.output),
                symbols=args.symbols,
                limit=args.limit,
            )
            panel_plot_map_json = json.dumps(result.panel_plot_map, sort_keys=True, separators=(',', ':'))
            panel_omitted_map_json = json.dumps(result.panel_omitted_map, sort_keys=True, separators=(',', ':'))
            print(f'rendered={result.output_path} selected={result.selected_markets_count} with_features={result.markets_with_feature_rows_count} plotted={result.plotted_series_count} omitted_crowding={result.omitted_for_crowding_count} skipped_no_features={result.skipped_no_feature_rows_count} skipped_no_cvd={result.skipped_no_usable_cvd_values_count} unresolved={result.unresolved_symbols_count} btc_price_market={result.btc_price_market} panel_plot_map={panel_plot_map_json} panel_omitted_map={panel_omitted_map_json}')
            return 0

        if args.command == 'run-once':
            result = run_once_pipeline(
                base_dir=base_dir,
                universe_path=Path(args.universe) if args.universe else None,
                interval=args.interval,
                lookback_hours=args.lookback_hours,
                symbols=args.symbols.split(',') if args.symbols else None,
                limit=args.limit,
                output=Path(args.output),
                discord=args.discord,
                dry_run=args.dry_run,
                rolling_hours=args.rolling_hours,
            )
            print(result.summary)
            return 0 if result.success else 1

        symbols = args.symbols.split(',') if args.symbols else None
        settings = load_settings(base_dir, args.universe)
        db_path = resolve_data_db_path(settings.db_path, base_dir)
        init_db(db_path)
        markets = load_markets_config(settings.markets_config_path)
        markets = [m for m in markets if m.enabled and m.coinalyze_symbol]
        if symbols:
            markets = resolve_markets_by_symbol(markets, symbols)
        if args.limit is not None:
            markets = markets[:args.limit]
        if not markets:
            logging.error('No symbols remain after filtering')
            return 1
        receiver = CoinalyzeOhlcvReceiver(settings, markets)
        ok = receiver.run(interval=args.interval, lookback_hours=args.lookback_hours, once=args.once)
        return 0 if ok else 1
    except ConfigError as exc:
        logging.error(str(exc))
        return 2
    except ValueError as exc:
        logging.error(str(exc))
        return 1

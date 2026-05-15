from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .ccxt_fetcher import CcxtOhlcvFetcher
from .charting import make_chart
from .constants import UTC
from .db import load_ohlcv_history, prune_db, save_ohlcv_bars
from .flow import classify_flow
from .models import MarketConfig, Settings
from .notifier import send_discord


class StablecoinMonitorApp:
    """Application service that coordinates fetch, store, render, and notify.

    The app layer deliberately contains orchestration only. It delegates data
    acquisition, persistence, flow classification, charting, and notification to
    separate modules.
    """

    def __init__(self, settings: Settings, markets: list[MarketConfig]) -> None:
        self.settings = settings
        self.markets = markets
        self.fetcher = CcxtOhlcvFetcher(settings)

    def fetch_and_store_snapshot(self) -> bool:
        try:
            bars = self.fetcher.fetch(self.markets)
            save_ohlcv_bars(self.settings.db_path, bars)
            deleted = prune_db(self.settings.db_path, self.settings.retention_days)
            if deleted:
                logging.info('Pruned %s old rows.', deleted)
            return bool(bars)
        except KeyboardInterrupt:
            raise
        except Exception:
            logging.exception('Fetch/store OHLCV snapshot failed.')
            return False

    def render_and_notify_chart(self) -> bool:
        try:
            history = load_ohlcv_history(self.settings.db_path, self.settings.history_days)
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
            chart_path = make_chart(self.settings, self.markets, history, ts_utc)
            logging.info('Chart saved: %s', chart_path)
        except KeyboardInterrupt:
            raise
        except Exception:
            logging.exception('Chart generation failed.')
            return False

        summary = classify_flow(self.settings, self.markets, history).to_message()
        if self.settings.discord_webhook_url:
            try:
                send_discord(
                    self.settings.discord_webhook_url,
                    summary,
                    chart_path,
                    self.settings.request_timeout_seconds,
                )
                logging.info('Discord notification sent.')
            except KeyboardInterrupt:
                raise
            except Exception:
                logging.exception('Discord notification failed.')
        else:
            logging.info('DISCORD_WEBHOOK_URL not set. Chart saved locally only.')
            print(summary)

        return True

    def run_once(self) -> bool:
        if not self.fetch_and_store_snapshot():
            return False
        return self.render_and_notify_chart()

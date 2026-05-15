from __future__ import annotations

import logging
from typing import Any

try:
    import ccxt  # type: ignore
except ImportError:  # pragma: no cover - handled at runtime
    ccxt = None

from .exceptions import ConfigError
from .market_registry import enabled_markets
from .models import MarketConfig, OhlcvBar, Settings
from .proxy_cvd import parse_ohlcv_rows


class CcxtOhlcvFetcher:
    """Fetch public OHLCV bars through CCXT.

    This class owns exchange object creation, market loading, rate-limit aware
    CCXT calls, and market-level failure isolation. It deliberately does not
    know about SQLite, charting, or flow classification.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._exchange_cache: dict[str, Any] = {}

    def fetch(self, markets: list[MarketConfig]) -> list[OhlcvBar]:
        bars: list[OhlcvBar] = []
        for market in enabled_markets(markets):
            try:
                bars.extend(self.fetch_market(market))
            except KeyboardInterrupt:
                raise
            except Exception:
                logging.exception('OHLCV fetch failed for %s', market.market_key)
        return bars

    def fetch_market(self, market: MarketConfig) -> list[OhlcvBar]:
        exchange = self._exchange_for(market.exchange)
        if not exchange.has.get('fetchOHLCV'):
            logging.warning('%s does not support fetchOHLCV; skip %s', market.exchange, market.symbol)
            return []

        timeframe = self._choose_supported_timeframe(exchange, self.settings.ohlcv_timeframe)
        if timeframe is None:
            logging.warning('%s has no usable OHLCV timeframe; skip %s', market.exchange, market.symbol)
            return []

        if market.symbol not in exchange.markets:
            logging.warning('%s does not list %s; skip', market.exchange, market.symbol)
            return []

        raw_rows = exchange.fetch_ohlcv(
            market.symbol,
            timeframe=timeframe,
            limit=self.settings.ohlcv_limit,
        )
        parsed = parse_ohlcv_rows(market, timeframe, raw_rows)
        logging.info('Fetched %s OHLCV bars for %s', len(parsed), market.market_key)
        return parsed

    def _exchange_for(self, exchange_id: str) -> Any:
        cached = self._exchange_cache.get(exchange_id)
        if cached is not None:
            return cached

        if ccxt is None:
            raise ConfigError('ccxt is not installed. Run: pip install -r requirements.txt')
        if not hasattr(ccxt, exchange_id):
            raise ConfigError(f'ccxt does not support exchange: {exchange_id}')

        exchange_class = getattr(ccxt, exchange_id)
        exchange = exchange_class({
            'enableRateLimit': True,
            'timeout': self.settings.request_timeout_seconds * 1000,
        })
        exchange.load_markets()
        self._exchange_cache[exchange_id] = exchange
        return exchange

    @staticmethod
    def _choose_supported_timeframe(exchange: Any, preferred: str) -> str | None:
        timeframes = getattr(exchange, 'timeframes', None) or {}
        if not timeframes:
            return preferred
        if preferred in timeframes:
            return preferred
        for fallback in ('1m', '3m', '5m', '15m', '1h'):
            if fallback in timeframes:
                return fallback
        return None

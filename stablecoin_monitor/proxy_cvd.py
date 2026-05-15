from __future__ import annotations

from datetime import datetime

from .constants import UTC
from .exceptions import ConfigError
from .models import MarketConfig, OhlcvBar


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

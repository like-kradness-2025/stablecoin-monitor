from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

Category = Literal['btc_stable', 'stable_fiat', 'stable_cross', 'other']


@dataclass(slots=True)
class Settings:
    discord_webhook_url: str | None
    db_path: Path
    output_dir: Path
    poll_interval_seconds: int
    fetch_interval_seconds: int
    render_interval_seconds: int
    history_days: int
    retention_days: int
    request_timeout_seconds: int
    markets_config_path: Path
    ohlcv_timeframe: str
    ohlcv_limit: int
    flow_delta_threshold_quote: float


@dataclass(slots=True)
class MarketConfig:
    market_key: str
    exchange: str
    symbol: str
    base_symbol: str
    quote_symbol: str
    category: str
    priority: int
    enabled: bool


@dataclass(slots=True)
class OhlcvBar:
    bucket_start_utc: datetime
    market_key: str
    exchange: str
    symbol: str
    base_symbol: str
    quote_symbol: str
    category: str
    timeframe: str
    timeframe_seconds: int
    open: float
    high: float
    low: float
    close: float
    volume_base: float
    volume_quote: float
    proxy_delta_base: float
    proxy_delta_quote: float


@dataclass(slots=True)
class FlowSummary:
    signal: str
    note: str
    latest_btc_price: float
    btc_stable_delta_quote: float
    stable_fiat_delta_quote: float
    stable_cross_delta_quote: float

    def to_message(self) -> str:
        from .formatting import human_price, human_volume

        return (
            f'BTC {human_price(self.latest_btc_price)} | Signal: {self.signal} | '
            f'BTC/stable {human_volume(self.btc_stable_delta_quote)} | '
            f'stable/fiat {human_volume(self.stable_fiat_delta_quote)} | '
            f'stable/cross {human_volume(self.stable_cross_delta_quote)}\n'
            f'{self.note}'
        )

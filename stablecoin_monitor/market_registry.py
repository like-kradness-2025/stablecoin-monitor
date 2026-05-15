from __future__ import annotations

from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - handled at runtime
    yaml = None

from .constants import ALLOWED_CATEGORIES
from .exceptions import ConfigError
from .models import MarketConfig


def load_markets_config(path: Path) -> list[MarketConfig]:
    if yaml is None:
        raise ConfigError('PyYAML is not installed. Run: pip install -r requirements.txt')
    if not path.exists():
        raise ConfigError(f'Markets config not found: {path}')

    payload = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    raw_markets = payload.get('markets')
    if not isinstance(raw_markets, list) or not raw_markets:
        raise ConfigError('markets.yaml must contain a non-empty "markets" list')

    markets: list[MarketConfig] = []
    for idx, raw in enumerate(raw_markets, start=1):
        if not isinstance(raw, dict):
            raise ConfigError(f'markets[{idx}] must be an object')

        exchange = str(raw.get('exchange', '')).strip().lower()
        symbol = str(raw.get('symbol', '')).strip().upper()
        base_symbol = str(raw.get('base', raw.get('base_symbol', ''))).strip().upper()
        quote_symbol = str(raw.get('quote', raw.get('quote_symbol', ''))).strip().upper()
        category = str(raw.get('category', 'other')).strip().lower()
        market_key = str(raw.get('market_key', f'{exchange}:{symbol}')).strip().lower()
        enabled = bool(raw.get('enabled', True))
        priority = int(raw.get('priority', 100))

        markets.append(
            MarketConfig(
                market_key=market_key,
                exchange=exchange,
                symbol=symbol,
                base_symbol=base_symbol,
                quote_symbol=quote_symbol,
                category=category,
                priority=priority,
                enabled=enabled,
            )
        )

    validate_markets(markets)
    return sorted(markets, key=lambda item: item.priority)


def validate_markets(markets: list[MarketConfig]) -> None:
    seen: set[str] = set()
    for market in markets:
        if not market.exchange:
            raise ConfigError('market exchange must not be empty')
        if not market.symbol or '/' not in market.symbol:
            raise ConfigError(f'invalid market symbol for {market.market_key}: {market.symbol}')
        if not market.base_symbol or not market.quote_symbol:
            raise ConfigError(f'base/quote must not be empty for {market.market_key}')
        if market.base_symbol == market.quote_symbol:
            raise ConfigError(f'base and quote must differ for {market.market_key}')
        if market.category not in ALLOWED_CATEGORIES:
            raise ConfigError(f'invalid category for {market.market_key}: {market.category}')
        expected_key = f'{market.exchange}:{market.symbol}'.lower()
        if market.market_key != expected_key:
            raise ConfigError(f'market_key must be "{expected_key}", got "{market.market_key}"')
        if market.market_key in seen:
            raise ConfigError(f'duplicate market_key: {market.market_key}')
        seen.add(market.market_key)


def enabled_markets(markets: list[MarketConfig]) -> list[MarketConfig]:
    return [market for market in markets if market.enabled]


def market_label(market_key: str) -> str:
    exchange, symbol = market_key.split(':', 1)
    return f'{exchange} {symbol.upper()}'

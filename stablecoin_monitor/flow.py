from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .constants import UTC
from .models import FlowSummary, MarketConfig, Settings


def category_recent_delta(
    history: dict[str, list[dict[str, Any]]],
    markets: list[MarketConfig],
    category: str,
    lookback_minutes: int = 15,
) -> float:
    cutoff = datetime.now(UTC) - timedelta(minutes=lookback_minutes)
    total = 0.0
    for market in markets:
        if market.category != category:
            continue
        for row in history.get(market.market_key, []):
            if row['bucket_start_utc'] >= cutoff:
                total += float(row['proxy_delta_quote'] or 0.0)
    return total


def pick_primary_btc_market(markets: list[MarketConfig], history: dict[str, list[dict[str, Any]]]) -> str | None:
    preferred = ['binance:btc/usdt', 'binance:btc/fdusd', 'coinbase:btc/usdc']
    for key in preferred:
        if key in history and history[key]:
            return key
    for market in sorted(markets, key=lambda item: item.priority):
        if market.category == 'btc_stable' and market.market_key in history and history[market.market_key]:
            return market.market_key
    return None


def latest_btc_price(markets: list[MarketConfig], history: dict[str, list[dict[str, Any]]]) -> float:
    primary_key = pick_primary_btc_market(markets, history)
    if not primary_key:
        return 0.0
    return float(history[primary_key][-1]['close'])


def classify_flow(settings: Settings, markets: list[MarketConfig], history: dict[str, list[dict[str, Any]]]) -> FlowSummary:
    btc_delta = category_recent_delta(history, markets, 'btc_stable')
    fiat_delta = category_recent_delta(history, markets, 'stable_fiat')
    cross_delta = category_recent_delta(history, markets, 'stable_cross')
    threshold = settings.flow_delta_threshold_quote

    if btc_delta < -threshold and fiat_delta < -threshold:
        signal = 'FIAT_EXIT'
        note = 'BTC売りとフィアット出口が同時に強い'
    elif btc_delta > threshold and fiat_delta >= 0:
        signal = 'BTC_DEMAND'
        note = 'ステーブル資金がBTCへ向かうバイアス'
    elif fiat_delta > threshold and abs(btc_delta) < threshold:
        signal = 'STABLE_INFLOW'
        note = 'フィアットからステーブル流入、BTC反応はまだ弱い'
    elif abs(cross_delta) > threshold and abs(btc_delta) < threshold:
        signal = 'STABLE_ROTATION'
        note = 'ステーブル間の乗り換え需要が優勢'
    elif btc_delta < -threshold:
        signal = 'BTC_RISK_OFF'
        note = 'BTCからステーブルへ逃避するバイアス'
    else:
        signal = 'MIXED'
        note = '方向感は混在。断定しない'

    return FlowSummary(
        signal=signal,
        note=note,
        latest_btc_price=latest_btc_price(markets, history),
        btc_stable_delta_quote=btc_delta,
        stable_fiat_delta_quote=fiat_delta,
        stable_cross_delta_quote=cross_delta,
    )

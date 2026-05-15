from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from .constants import JST
from .flow import classify_flow, pick_primary_btc_market
from .market_registry import market_label
from .models import MarketConfig, Settings


def apply_dashboard_theme() -> None:
    plt.style.use('dark_background')
    plt.rcParams.update({
        'figure.facecolor': '#07111f',
        'axes.facecolor': '#0d1626',
        'axes.edgecolor': '#2a3a57',
        'axes.labelcolor': '#dbe7ff',
        'xtick.color': '#9fb0c9',
        'ytick.color': '#9fb0c9',
        'text.color': '#eef4ff',
        'axes.titlecolor': '#f5f8ff',
        'grid.color': '#27405f',
        'grid.alpha': 0.28,
        'font.size': 10,
        'axes.titlesize': 11,
        'axes.labelsize': 9,
        'legend.fontsize': 7,
    })


def plot_category_cvd(
    ax: plt.Axes,
    history: dict[str, list[dict[str, Any]]],
    markets: list[MarketConfig],
    category: str,
) -> None:
    plotted = 0
    for market in sorted(markets, key=lambda item: item.priority):
        if market.category != category:
            continue
        rows = history.get(market.market_key, [])
        if not rows:
            continue
        x = [row['bucket_start_utc'].astimezone(JST) for row in rows]
        y = [float(row['proxy_cvd_quote'] or 0.0) / 1_000_000 for row in rows]
        ax.plot(x, y, linewidth=1.0, label=market_label(market.market_key))
        plotted += 1
        if plotted >= 8:
            break
    ax.axhline(0.0, linestyle='--', linewidth=0.7, color='#a5b8d6')
    ax.set_ylabel('Proxy CVD quote mn')
    ax.grid(True, alpha=0.22)
    if plotted:
        ax.legend(loc='upper left', ncol=2, frameon=False)


def make_chart(
    settings: Settings,
    markets: list[MarketConfig],
    history: dict[str, list[dict[str, Any]]],
    now_utc: datetime,
) -> Path:
    apply_dashboard_theme()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    chart_path = settings.output_dir / f"stablecoin_monitor_v3_{now_utc.astimezone(JST).strftime('%Y%m%d_%H%M%S')}.png"

    fig = plt.figure(figsize=(12, 8))
    fig.patch.set_facecolor('#07111f')
    grid = fig.add_gridspec(4, 1, height_ratios=[1.15, 1.0, 1.0, 0.9], hspace=0.22)

    ax_price = fig.add_subplot(grid[0, 0])
    ax_btc_cvd = fig.add_subplot(grid[1, 0], sharex=ax_price)
    ax_fiat_cvd = fig.add_subplot(grid[2, 0], sharex=ax_price)
    ax_cross_cvd = fig.add_subplot(grid[3, 0], sharex=ax_price)

    primary_key = pick_primary_btc_market(markets, history)
    if primary_key:
        rows = history[primary_key]
        x = [row['bucket_start_utc'].astimezone(JST) for row in rows]
        y = [float(row['close']) for row in rows]
        ax_price.plot(x, y, linewidth=1.35, color='#f5a524', label=f'BTC price ({market_label(primary_key)})')
        ax_price.legend(loc='upper left', frameon=False)

    ax_price.set_title('Stablecoin Flow Monitor v3.00 - CCXT OHLCV Proxy CVD', fontweight='bold')
    ax_price.set_ylabel('BTC price')
    ax_price.grid(True, alpha=0.22)

    ax_btc_cvd.set_title('BTC / Stable Proxy CVD', loc='left', pad=4, fontweight='bold')
    plot_category_cvd(ax_btc_cvd, history, markets, 'btc_stable')

    ax_fiat_cvd.set_title('Stable / Fiat Proxy CVD', loc='left', pad=4, fontweight='bold')
    plot_category_cvd(ax_fiat_cvd, history, markets, 'stable_fiat')

    ax_cross_cvd.set_title('Stable / Stable Proxy CVD', loc='left', pad=4, fontweight='bold')
    plot_category_cvd(ax_cross_cvd, history, markets, 'stable_cross')

    locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
    formatter = mdates.DateFormatter('%m-%d %H:%M', tz=JST)
    for ax in (ax_price, ax_btc_cvd, ax_fiat_cvd, ax_cross_cvd):
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)
        ax.tick_params(axis='both', labelsize=7)
    for ax in (ax_price, ax_btc_cvd, ax_fiat_cvd):
        ax.tick_params(labelbottom=False)

    flow_summary = classify_flow(settings, markets, history)
    fig.suptitle(flow_summary.to_message().split('\n')[0], fontsize=10, color='#dbe7ff', y=0.975)
    fig.text(
        0.985,
        0.015,
        now_utc.astimezone(JST).strftime('%Y-%m-%d %H:%M JST'),
        ha='right',
        va='bottom',
        fontsize=7,
        color='#9fb0c9',
    )
    fig.subplots_adjust(left=0.07, right=0.985, top=0.94, bottom=0.08)
    fig.savefig(chart_path, dpi=150)
    plt.close(fig)
    return chart_path

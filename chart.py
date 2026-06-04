#!/usr/bin/env python3
"""
Stablecoin depeg + volume — Navy theme
Usage:
  python3 navy_chart.py --period 1d     # 24h
  python3 navy_chart.py --period 1w     # 7 days
  python3 navy_chart.py --period 1mo    # 30 days (default)
  python3 navy_chart.py --period 3mo    # 90 days
  python3 navy_chart.py --period 2026-05-01:2026-06-04  # custom range
"""
import argparse, sqlite3, sys
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MultipleLocator

# --- Argparse ---
parser = argparse.ArgumentParser(description="Stablecoin depeg chart")
parser.add_argument("--period", "-p", default="1mo",
                    help="Chart period: 1d, 1w, 1mo, 3mo, or YYYY-MM-DD:YYYY-MM-DD")
parser.add_argument("--output", "-o", default="/tmp/stablecoin_navy.png",
                    help="Output path")
parser.add_argument("--db", default="/tmp/stablecoin_monitor.db",
                    help="Stablecoin monitor DB path")
args = parser.parse_args()

# Parse period
now = datetime.now(timezone.utc)
if ":" in args.period:
    parts = args.period.split(":")
    start_str, end_str = parts[0], parts[1]
    start = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) if ":" in args.period else now
else:
    period_map = {
        "1d": timedelta(days=1), "24h": timedelta(days=1),
        "1w": timedelta(weeks=1), "7d": timedelta(weeks=1),
        "1mo": timedelta(days=30), "30d": timedelta(days=30),
        "3mo": timedelta(days=90),
    }
    delta = period_map.get(args.period, timedelta(days=30))
    start = now - delta
    end = now

period_label = args.period
days = (end - start).days
dur_h = (end - start).total_seconds() / 3600

# --- Style ---
plt.rcParams.update({
    "figure.facecolor": "#0a1628",
    "axes.facecolor": "#0f1f3d",
    "axes.edgecolor": "#35598a",
    "axes.labelcolor": "#cbd5e1",
    "xtick.color": "#cbd5e1",
    "ytick.color": "#cbd5e1",
    "text.color": "#e2e8f0",
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.labelsize": 11,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
})

COLOR = {
    "BTC": "#60a5fa",
    "USDT": "#cbd5e1",
    "USDC": "#38bdf8",
    "FDUSD": "#d8b4fe",
    "grid": "#334155",
    "zero": "#94a3b8",
    "neg_fill": "#ef4444",
}

# --- Data ---
start_iso = start.strftime("%Y-%m-%dT%H:%M:%S+00:00")
end_iso = end.strftime("%Y-%m-%dT%H:%M:%S+00:00")

conn = sqlite3.connect(args.db)
df = pd.read_sql_query(
    "SELECT ts_utc, symbol, price_usd, volume_24h_usd FROM snapshots "
    "WHERE ts_utc >= ? AND ts_utc <= ? ORDER BY ts_utc",
    conn, params=[start_iso, end_iso])
conn.close()

if df.empty:
    print(f"ERROR: no data for period {args.period} ({start_iso} ~ {end_iso})", file=sys.stderr)
    sys.exit(1)

df["dt"] = pd.to_datetime(df["ts_utc"])

# Resample interval based on period duration
if dur_h <= 48:
    resample_rule = "5min"
    vol_resample = "1h"
    vol_width = 0.03
    x_interval = 6  # hours
    x_fmt = "%H:%M"
elif dur_h <= 168:
    resample_rule = "15min"
    vol_resample = "6h"
    vol_width = 0.5
    x_interval = 1  # days
    x_fmt = "%m/%d"
elif dur_h <= 720:
    resample_rule = "1h"
    vol_resample = "1D"
    vol_width = 0.7
    x_interval = 3  # days
    x_fmt = "%m/%d"
else:
    resample_rule = "3h"
    vol_resample = "3D"
    vol_width = 0.8
    x_interval = 7  # days
    x_fmt = "%m/%d"

price_pivot = df.pivot_table(index="dt", columns="symbol", values="price_usd", aggfunc="first")
price_pivot = price_pivot.resample(resample_rule).first().dropna().copy()
price_pivot["USDT_depeg"] = (price_pivot["USDT"] - 1) * 10000
price_pivot["USDC_depeg"] = (price_pivot["USDC"] - 1) * 10000
price_pivot["FDUSD_depeg"] = (price_pivot["FDUSD"] - 1) * 10000

vol_pivot = df.pivot_table(index="dt", columns="symbol", values="volume_24h_usd", aggfunc="last")
vol_pivot = vol_pivot.resample(vol_resample).last().dropna().copy()
common_idx = price_pivot.index.intersection(vol_pivot.index)
prices = price_pivot.loc[common_idx]
volumes = vol_pivot.loc[common_idx]

# Dynamic y-limits
btc_min, btc_max = prices["BTC"].min(), prices["BTC"].max()
btc_pad = (btc_max - btc_min) * 0.08
usdt_min = prices["USDT_depeg"].min()
usdc_min = prices["USDC_depeg"].min()
fdusd_min = prices["FDUSD_depeg"].min()

# --- Plot ---
fig, axes = plt.subplots(4, 1, figsize=(14, 18), sharex=True,
                         gridspec_kw={'height_ratios': [2.2, 1.15, 1.15, 1.15]})
fig.suptitle(f"BTC Price + Stablecoin Depeg with Volume ({period_label})",
             fontsize=16, fontweight="bold", color="#f1f5f9", y=0.985)

def style_ax(ax):
    ax.tick_params(axis="both", colors="#cbd5e1", labelsize=9)
    for spine in ax.spines.values():
        spine.set_color("#35598a")
        spine.set_linewidth(0.8)
    ax.grid(True, color=COLOR["grid"], alpha=0.25, linewidth=0.7)

for ax in axes:
    style_ax(ax)

# Panel 1: BTC
ax1 = axes[0]
ax1.set_facecolor("#0f1f3d")
ax1.plot(prices.index, prices["BTC"], color=COLOR["BTC"], lw=1.8, zorder=3)
ax1.set_ylabel("BTC (USD)", fontsize=10, color=COLOR["BTC"])
ax1.set_title("BTC Price + Volume", fontsize=13, color="#e2e8f0", pad=6)
ax1.set_ylim(btc_min - btc_pad, btc_max + btc_pad)

ax1_t = ax1.twinx()
ax1_t.bar(volumes.index, volumes["BTC"] / 1e9, width=vol_width,
          color=COLOR["BTC"], alpha=0.14, zorder=1)
ax1_t.set_ylabel("BTC Vol ($B)", fontsize=9, color=COLOR["BTC"])
ax1_t.tick_params(axis="y", labelcolor=COLOR["BTC"], labelsize=8)
for sp in ax1_t.spines.values():
    sp.set_color("#35598a")

# Panel 2: USDT
ax2 = axes[1]
ax2.set_facecolor("#0f1f3d")
ax2.plot(prices.index, prices["USDT_depeg"], color=COLOR["USDT"], lw=1.5, zorder=3)
ax2.fill_between(prices.index, prices["USDT_depeg"], 0, where=(prices["USDT_depeg"] < 0),
                 color=COLOR["neg_fill"], alpha=0.1, zorder=1)
ax2.axhline(0, color=COLOR["zero"], lw=0.9, alpha=0.6, zorder=2)
ax2.set_ylabel("USDT depeg (bp)", fontsize=10, color=COLOR["USDT"])
ax2.set_title("USDT Depeg + Volume", fontsize=13, color="#e2e8f0", pad=6)
ax2_ybot = min(usdt_min - 2, -5)
ax2.set_ylim(ax2_ybot, 1)
ax2.yaxis.set_major_locator(MultipleLocator(5))

ax2_t = ax2.twinx()
ax2_t.bar(volumes.index, volumes["USDT"] / 1e9, width=vol_width,
          color=COLOR["USDT"], alpha=0.12, zorder=1)
ax2_t.set_ylabel("USDT Vol ($B)", fontsize=9, color=COLOR["USDT"])
ax2_t.tick_params(axis="y", labelcolor=COLOR["USDT"], labelsize=8)
for sp in ax2_t.spines.values():
    sp.set_color("#35598a")

# Annotations: top 5 extremes
extreme_count = min(5, max(1, len(prices) // 50))
extreme = prices.nsmallest(extreme_count, "USDT_depeg")
if len(extreme) > 0:
    for i, (dt, r) in enumerate(extreme.iterrows()):
        ax2.annotate(f"{r['USDT_depeg']:.1f} bp",
                     xy=(dt, r["USDT_depeg"]),
                     xytext=(0, -16 - i * 3),
                     textcoords="offset points",
                     ha="center", va="top",
                     fontsize=8, color="#fecaca",
                     bbox=dict(boxstyle="round,pad=0.2", facecolor="#7f1d1d",
                               edgecolor="none", alpha=0.7),
                     arrowprops=dict(arrowstyle="-", color="#f87171", lw=0.5, alpha=0.7))

# Panel 3: USDC
ax3 = axes[2]
ax3.set_facecolor("#0f1f3d")
ax3.plot(prices.index, prices["USDC_depeg"], color=COLOR["USDC"], lw=1.5, zorder=3)
ax3.fill_between(prices.index, prices["USDC_depeg"], 0, where=(prices["USDC_depeg"] < 0),
                 color=COLOR["USDC"], alpha=0.1, zorder=1)
ax3.axhline(0, color=COLOR["zero"], lw=0.9, alpha=0.6, zorder=2)
ax3.set_ylabel("USDC depeg (bp)", fontsize=10, color=COLOR["USDC"])
ax3.set_title("USDC Depeg + Volume", fontsize=13, color="#e2e8f0", pad=6)
usdc_ybot = min(usdc_min - 1, -3)
ax3.set_ylim(usdc_ybot, 1.5)
ax3.yaxis.set_major_locator(MultipleLocator(2))

ax3_t = ax3.twinx()
ax3_t.bar(volumes.index, volumes["USDC"] / 1e9, width=vol_width,
          color=COLOR["USDC"], alpha=0.12, zorder=1)
ax3_t.set_ylabel("USDC Vol ($B)", fontsize=9, color=COLOR["USDC"])
ax3_t.tick_params(axis="y", labelcolor=COLOR["USDC"], labelsize=8)
for sp in ax3_t.spines.values():
    sp.set_color("#35598a")

# Panel 4: FDUSD
ax4 = axes[3]
ax4.set_facecolor("#0f1f3d")
ax4.plot(prices.index, prices["FDUSD_depeg"], color=COLOR["FDUSD"], lw=1.5, zorder=3)
ax4.fill_between(prices.index, prices["FDUSD_depeg"], 0, where=(prices["FDUSD_depeg"] < 0),
                 color=COLOR["FDUSD"], alpha=0.1, zorder=1)
ax4.axhline(0, color=COLOR["zero"], lw=0.9, alpha=0.6, zorder=2)
ax4.set_ylabel("FDUSD depeg (bp)", fontsize=10, color=COLOR["FDUSD"])
ax4.set_title("FDUSD Depeg + Volume", fontsize=13, color="#e2e8f0", pad=6)
fdusd_ybot = min(fdusd_min - 2, -10)
ax4.set_ylim(fdusd_ybot, 2)
ax4.yaxis.set_major_locator(MultipleLocator(10))

ax4_t = ax4.twinx()
ax4_t.bar(volumes.index, volumes["FDUSD"] / 1e9, width=vol_width,
          color=COLOR["FDUSD"], alpha=0.14, zorder=1)
ax4_t.set_ylabel("FDUSD Vol ($B)", fontsize=9, color=COLOR["FDUSD"])
ax4_t.tick_params(axis="y", labelcolor=COLOR["FDUSD"], labelsize=8)
for sp in ax4_t.spines.values():
    sp.set_color("#35598a")

# X-axis formatting
if dur_h <= 48:
    ax4.xaxis.set_major_formatter(mdates.DateFormatter(x_fmt))
    ax4.xaxis.set_major_locator(mdates.HourLocator(interval=x_interval))
elif dur_h <= 168:
    ax4.xaxis.set_major_formatter(mdates.DateFormatter(x_fmt))
    ax4.xaxis.set_major_locator(mdates.DayLocator(interval=x_interval))
else:
    ax4.xaxis.set_major_formatter(mdates.DateFormatter(x_fmt))
    ax4.xaxis.set_major_locator(mdates.DayLocator(interval=x_interval))

plt.setp(ax4.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=9, color="#cbd5e1")

plt.subplots_adjust(hspace=0.2, top=0.95, bottom=0.07, left=0.07, right=0.93)
plt.savefig(args.output, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
plt.close()
print(f"SAVED:{args.output}")

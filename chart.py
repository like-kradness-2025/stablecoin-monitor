#!/usr/bin/env python3
"""
Stablecoin depeg + volume — Navy theme v4
Variable period. All sounding-board feedback incorporated.
"""
import argparse, sqlite3, sys
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MultipleLocator, FuncFormatter

parser = argparse.ArgumentParser(description="Stablecoin depeg chart")
parser.add_argument("--period", "-p", default="1mo", help="1d, 1w, 1mo, 3mo, or YYYY-MM-DD:YYYY-MM-DD")
parser.add_argument("--output", "-o", default="/tmp/stablecoin_navy.png")
parser.add_argument("--db", default="/tmp/stablecoin_monitor.db")
args = parser.parse_args()

now = datetime.now(timezone.utc)
if ":" in args.period:
    parts = args.period.split(":")
    start = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(parts[1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
else:
    period_map = {"1d": timedelta(days=1), "24h": timedelta(days=1),
                  "1w": timedelta(weeks=1), "7d": timedelta(weeks=1),
                  "1mo": timedelta(days=30), "30d": timedelta(days=30),
                  "3mo": timedelta(days=90)}
    delta = period_map.get(args.period, timedelta(days=30))
    start = now - delta
    end = now

period_label = args.period
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
    "axes.titlesize": 12,
    "axes.labelsize": 11,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
})

COLOR = {"BTC":"#60a5fa","USDT":"#cbd5e1","USDC":"#38bdf8","FDUSD":"#d8b4fe",
         "grid":"#334155","zero":"#94a3b8","neg_fill":"#ef4444"}

# Fill thresholds for negative fill (bp)
FILL_THRESH = {"USDT": -10, "USDC": -3, "FDUSD": -20}

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
    print(f"ERROR: no data for {args.period} ({start_iso} ~ {end_iso})", file=sys.stderr)
    sys.exit(1)

df["dt"] = pd.to_datetime(df["ts_utc"])

# Auto-resample
if dur_h <= 48:
    r, vr, vw, xi, xf = "5min", "1h", 0.025, 6, "%H:%M"
elif dur_h <= 168:
    r, vr, vw, xi, xf = "15min", "6h", 0.35, 1, "%m/%d"
elif dur_h <= 720:
    r, vr, vw, xi, xf = "1h", "1D", 0.55, 3, "%m/%d"
else:
    r, vr, vw, xi, xf = "3h", "3D", 0.65, 7, "%m/%d"

price_pivot = df.pivot_table(index="dt", columns="symbol", values="price_usd", aggfunc="first")
price_pivot = price_pivot.resample(r).first().dropna().copy()
price_pivot["USDT_depeg"] = (price_pivot["USDT"] - 1) * 10000
price_pivot["USDC_depeg"] = (price_pivot["USDC"] - 1) * 10000
price_pivot["FDUSD_depeg"] = (price_pivot["FDUSD"] - 1) * 10000

vol_pivot = df.pivot_table(index="dt", columns="symbol", values="volume_24h_usd", aggfunc="last")
vol_pivot = vol_pivot.resample(vr).last().dropna().copy()
common_idx = price_pivot.index.intersection(vol_pivot.index)
prices = price_pivot.loc[common_idx]
volumes = vol_pivot.loc[common_idx]

# --- Plot ---
fig, axes = plt.subplots(4, 1, figsize=(14, 16), sharex=True,
                         gridspec_kw={'height_ratios': [2.2, 1.15, 1.15, 1.15]})
fig.suptitle(f"BTC Price + Stablecoin Depeg with Volume ({period_label})",
             fontsize=15, fontweight="bold", color="#f1f5f9", y=0.985)

def style_ax(ax):
    ax.tick_params(axis="both", colors="#cbd5e1", labelsize=9)
    for sp in ax.spines.values():
        sp.set_color("#35598a"); sp.set_linewidth(0.8)
    ax.grid(True, color=COLOR["grid"], alpha=0.22, linewidth=0.7)

for ax in axes: style_ax(ax)

def add_vol_axis(ax, sym, vol_series, vol_width):
    """Add volume axis with bars, mean line, zero-based, dynamic unit."""
    ax_t = ax.twinx()
    # Dynamic unit: $B for >=1B, $M for <1B
    v_max_raw = vol_series.max()
    if v_max_raw < 1e9:
        v = vol_series / 1e6       # convert to millions
        unit = "$M"
        fmt = lambda x, _: f"{x:.0f}"
    else:
        v = vol_series / 1e9       # convert to billions
        unit = "$B"
        fmt = lambda x, _: f"{x:.0f}"
    
    # Volume bars — zero-based
    ax_t.bar(v.index, v.values, width=vol_width,
             color=COLOR[sym], alpha=0.08, zorder=0)
    
    # Mean volume line — subtle
    v_mean = v.mean()
    ax_t.axhline(v_mean, color=COLOR[sym], lw=0.7, ls=":", alpha=0.35, zorder=1)
    
    # Volume y-axis: zero-based
    v_max = v.max()
    ax_t.set_ylim(0, v_max * 1.18 if v_max > 0 else 1)
    
    # Format y-axis labels
    ax_t.yaxis.set_major_formatter(FuncFormatter(fmt))
    ax_t.set_ylabel(f"{sym} Vol ({unit})", fontsize=9, color=COLOR[sym])
    ax_t.tick_params(axis="y", labelcolor=COLOR[sym], labelsize=8)
    
    # Mean volume annotation — top-right
    ax_t.text(0.985, 0.88, f"avg ${v_mean:.0f}{unit[-1]}",
              transform=ax_t.transAxes, fontsize=7, color=COLOR[sym], alpha=0.6,
              ha="right", va="top",
              bbox=dict(boxstyle="round,pad=0.15", facecolor="#0a1628",
                        edgecolor=COLOR[sym], alpha=0.25))
    
    # Ensure main axis is on top (bars behind)
    ax.set_zorder(ax_t.get_zorder() + 1)
    ax.patch.set_visible(False)
    
    for sp in ax_t.spines.values():
        sp.set_color("#35598a")
    return ax_t

# --- Panel 1: BTC ---
ax1 = axes[0]
ax1.set_facecolor("#0f1f3d")
ax1.plot(prices.index, prices["BTC"], color=COLOR["BTC"], lw=1.8, zorder=3)
ax1.set_ylabel("BTC (USD)", fontsize=10, color=COLOR["BTC"])
ax1.set_title("BTC Price + Volume", fontsize=12, color="#e2e8f0", pad=4)
btc_pad = (prices["BTC"].max() - prices["BTC"].min()) * 0.08
ax1.set_ylim(prices["BTC"].min() - btc_pad, prices["BTC"].max() + btc_pad)
add_vol_axis(ax1, "BTC", volumes["BTC"], vw)

# --- Panel 2: USDT ---
ax2 = axes[1]
ax2.set_facecolor("#0f1f3d")
ax2.plot(prices.index, prices["USDT_depeg"], color=COLOR["USDT"], lw=1.5, zorder=3)
th = FILL_THRESH["USDT"]
ax2.fill_between(prices.index, prices["USDT_depeg"], th,
                 where=(prices["USDT_depeg"] < th),
                 color=COLOR["neg_fill"], alpha=0.06, zorder=1)
ax2.axhline(0, color=COLOR["zero"], lw=0.9, alpha=0.45, zorder=2)
ax2.axhline(th, color=COLOR["neg_fill"], lw=0.5, ls=":", alpha=0.25, zorder=2)
ax2.set_ylabel("USDT depeg (bp)", fontsize=10, color=COLOR["USDT"])
ax2.set_title("USDT Depeg + Volume", fontsize=12, color="#e2e8f0", pad=4)
usdt_min = prices["USDT_depeg"].min()
ax2.set_ylim(min(usdt_min - 3.5, -5), 1)
ax2.yaxis.set_major_locator(MultipleLocator(5))
add_vol_axis(ax2, "USDT", volumes["USDT"], vw)

# Top 5 extremes — avoid bottom edge
extreme = prices.nsmallest(min(5, max(1, len(prices)//50)), "USDT_depeg")
for i, (dt, r) in enumerate(extreme.iterrows()):
    is_bottom = r["USDT_depeg"] < usdt_min * 0.7
    offset_y = (12 if is_bottom else -16) + (i * 2 if is_bottom else -i * 3)
    va = "bottom" if is_bottom else "top"
    ax2.annotate(f"{r['USDT_depeg']:.1f} bp",
                 xy=(dt, r["USDT_depeg"]),
                 xytext=(0, offset_y), textcoords="offset points",
                 ha="center", va=va, fontsize=8, color="#fecaca",
                 bbox=dict(boxstyle="round,pad=0.2", facecolor="#7f1d1d",
                           edgecolor="none", alpha=0.55),
                 arrowprops=dict(arrowstyle="-", color="#f87171", lw=0.5, alpha=0.6))

# --- Panel 3: USDC ---
ax3 = axes[2]
ax3.set_facecolor("#0f1f3d")
ax3.plot(prices.index, prices["USDC_depeg"], color=COLOR["USDC"], lw=1.5, zorder=3)
th3 = FILL_THRESH["USDC"]
ax3.fill_between(prices.index, prices["USDC_depeg"], th3,
                 where=(prices["USDC_depeg"] < th3),
                 color=COLOR["USDC"], alpha=0.06, zorder=1)
ax3.axhline(0, color=COLOR["zero"], lw=0.9, alpha=0.45, zorder=2)
ax3.axhline(th3, color=COLOR["USDC"], lw=0.5, ls=":", alpha=0.25, zorder=2)
ax3.set_ylabel("USDC depeg (bp)", fontsize=10, color=COLOR["USDC"])
ax3.set_title("USDC Depeg + Volume", fontsize=12, color="#e2e8f0", pad=4)
ax3.set_ylim(min(prices["USDC_depeg"].min() - 1.5, -3), 0.8)
ax3.yaxis.set_major_locator(MultipleLocator(2))
add_vol_axis(ax3, "USDC", volumes["USDC"], vw)

# --- Panel 4: FDUSD ---
ax4 = axes[3]
ax4.set_facecolor("#0f1f3d")
ax4.plot(prices.index, prices["FDUSD_depeg"], color=COLOR["FDUSD"], lw=1.5, zorder=3)
th4 = FILL_THRESH["FDUSD"]
ax4.fill_between(prices.index, prices["FDUSD_depeg"], th4,
                 where=(prices["FDUSD_depeg"] < th4),
                 color=COLOR["FDUSD"], alpha=0.06, zorder=1)
ax4.axhline(0, color=COLOR["zero"], lw=0.9, alpha=0.45, zorder=2)
ax4.axhline(th4, color=COLOR["FDUSD"], lw=0.5, ls=":", alpha=0.25, zorder=2)
ax4.set_ylabel("FDUSD depeg (bp)", fontsize=10, color=COLOR["FDUSD"])
ax4.set_title("FDUSD Depeg + Volume", fontsize=12, color="#e2e8f0", pad=4)
ax4.set_ylim(min(prices["FDUSD_depeg"].min() - 3, -10), 2)
ax4.yaxis.set_major_locator(MultipleLocator(10))
add_vol_axis(ax4, "FDUSD", volumes["FDUSD"], vw)

# X-axis
if dur_h <= 48:
    ax4.xaxis.set_major_formatter(mdates.DateFormatter(xf))
    ax4.xaxis.set_major_locator(mdates.HourLocator(interval=xi))
else:
    ax4.xaxis.set_major_formatter(mdates.DateFormatter(xf))
    ax4.xaxis.set_major_locator(mdates.DayLocator(interval=xi))

plt.setp(ax4.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=9, color="#cbd5e1")
plt.subplots_adjust(hspace=0.16, top=0.94, bottom=0.07, left=0.07, right=0.93)
plt.savefig(args.output, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
plt.close()
print(f"SAVED:{args.output}")

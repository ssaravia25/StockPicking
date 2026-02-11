"""
Plot a short candidate with full technical analysis.
Shows price, SMAs, stage shading, short entry/exit zones, and volume.

Usage:
    python3 tools/plot_short_candidate.py PAYX
    python3 tools/plot_short_candidate.py NFLX
"""

import os
import sys
import logging

import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.screener import classify_stage_vectorized, calculate_short_score_vectorized

logging.basicConfig(level=logging.WARNING)

STAGE_COLORS = {
    "Stage 2": "#c8e6c9",       # green
    "Stage 3": "#fff9c4",       # yellow
    "Stage 4": "#ffcdd2",       # red
    "Stage 1 / Neutral": "#e0e0e0",  # gray
}


def prepare(symbol: str, period: str = "2y") -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period)
    df["SMA10"] = df["Close"].rolling(10).mean()
    df["SMA20"] = df["Close"].rolling(20).mean()
    df["SMA50"] = df["Close"].rolling(50).mean()
    df["SMA150"] = df["Close"].rolling(150).mean()
    df["SMA200"] = df["Close"].rolling(200).mean()
    df["SMA200_prev20"] = df["SMA200"].shift(20)
    df["Vol_Avg50"] = df["Volume"].rolling(50).mean()
    df["Stage_Daily"] = classify_stage_vectorized(df)
    df["Short_Score"] = calculate_short_score_vectorized(df)

    # ATR
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    df["ATR"] = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()

    return df.dropna(subset=["SMA200", "SMA200_prev20"])


def simulate_shorts(df: pd.DataFrame):
    """Run bear_flag + time_stop on a single stock and return entry/exit points."""
    entries = []
    exits = []
    position = 0
    entry_price = 0
    entry_idx = None
    days = 0

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        stage = row["Stage_Daily"]

        if position == 0:
            if stage == "Stage 4" and row["Short_Score"] <= 7:
                position = -1
                entry_price = row["Close"]
                entry_idx = df.index[i]
                days = 0
                entries.append((df.index[i], row["Close"]))
        else:
            days += 1
            exit_now = False
            if stage in ("Stage 2", "Stage 1 / Neutral"):
                exit_now = True
            if days >= 40:
                exit_now = True
            if exit_now:
                ret = (entry_price - row["Close"]) / entry_price * 100
                exits.append((df.index[i], row["Close"], ret))
                position = 0

    # Close if still open
    if position == -1:
        last = df.iloc[-1]
        ret = (entry_price - last["Close"]) / entry_price * 100
        exits.append((df.index[-1], last["Close"], ret))

    return entries, exits


def plot(symbol: str):
    os.makedirs(".tmp", exist_ok=True)

    df = prepare(symbol)
    entries, exits = simulate_shorts(df)

    fig, axes = plt.subplots(
        3, 1, figsize=(18, 13),
        gridspec_kw={"height_ratios": [4, 1, 1]},
        sharex=True,
    )
    fig.suptitle(
        f"{symbol} — Short Analysis (Bear Flag + Time Stop 40d)",
        fontsize=16, fontweight="bold", y=0.98,
    )

    # ─── Panel 1: Price + SMAs + Stage shading ───
    ax = axes[0]
    dates = df.index

    # Stage background
    prev_stage = df["Stage_Daily"].iloc[0]
    stage_start = dates[0]
    for i in range(1, len(df)):
        curr = df["Stage_Daily"].iloc[i]
        if curr != prev_stage or i == len(df) - 1:
            color = STAGE_COLORS.get(prev_stage, "#f5f5f5")
            ax.axvspan(stage_start, dates[i], alpha=0.35, color=color, linewidth=0)
            stage_start = dates[i]
            prev_stage = curr

    # Price + SMAs
    ax.plot(dates, df["Close"], color="black", linewidth=1.5, label="Price", zorder=5)
    ax.plot(dates, df["SMA10"], color="#42a5f5", linewidth=0.8, alpha=0.7, label="SMA10")
    ax.plot(dates, df["SMA20"], color="#1565c0", linewidth=1, alpha=0.8, label="SMA20")
    ax.plot(dates, df["SMA50"], color="#e65100", linewidth=1.2, label="SMA50")
    ax.plot(dates, df["SMA150"], color="#6a1b9a", linewidth=1.2, label="SMA150")
    ax.plot(dates, df["SMA200"], color="#c62828", linewidth=1.5, label="SMA200")

    # Entry/exit markers
    for d, p in entries:
        ax.annotate(
            "SHORT", xy=(d, p), fontsize=8, fontweight="bold", color="white",
            ha="center", va="bottom",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#d32f2f", alpha=0.9),
            arrowprops=dict(arrowstyle="->", color="#d32f2f", lw=1.5),
            xytext=(d, p * 1.04),
        )
        ax.scatter([d], [p], color="#d32f2f", s=100, zorder=10, marker="v")

    for d, p, ret in exits:
        color = "#2e7d32" if ret > 0 else "#c62828"
        label = f"EXIT {ret:+.1f}%"
        ax.annotate(
            label, xy=(d, p), fontsize=8, fontweight="bold", color="white",
            ha="center", va="top",
            bbox=dict(boxstyle="round,pad=0.3", facecolor=color, alpha=0.9),
            arrowprops=dict(arrowstyle="->", color=color, lw=1.5),
            xytext=(d, p * 0.96),
        )
        ax.scatter([d], [p], color=color, s=100, zorder=10, marker="^")

    # Current stage + short score annotation
    latest = df.iloc[-1]
    info = f"Stage: {latest['Stage_Daily']}\nShort Score: {int(latest['Short_Score'])}\nPrice: ${latest['Close']:.2f}"
    ax.text(
        0.01, 0.97, info, transform=ax.transAxes, fontsize=10,
        verticalalignment="top", fontfamily="monospace",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.9),
    )

    # Stage legend
    stage_patches = [
        Patch(facecolor="#c8e6c9", alpha=0.5, label="Stage 2 (Uptrend)"),
        Patch(facecolor="#fff9c4", alpha=0.5, label="Stage 3 (Distribution)"),
        Patch(facecolor="#ffcdd2", alpha=0.5, label="Stage 4 (Downtrend)"),
        Patch(facecolor="#e0e0e0", alpha=0.5, label="Stage 1 (Neutral)"),
    ]
    legend1 = ax.legend(handles=stage_patches, loc="lower left", fontsize=8, ncol=2)
    ax.add_artist(legend1)
    ax.legend(loc="upper right", fontsize=9)
    ax.set_ylabel("Price ($)", fontsize=11)
    ax.grid(True, alpha=0.2)

    # ─── Panel 2: Volume ───
    ax2 = axes[1]
    colors_vol = ["#ef5350" if df["Close"].iloc[i] < df["Close"].iloc[i-1] else "#66bb6a"
                  for i in range(1, len(df))]
    colors_vol.insert(0, "#66bb6a")
    ax2.bar(dates, df["Volume"], color=colors_vol, alpha=0.6, width=1)
    ax2.plot(dates, df["Vol_Avg50"], color="#1565c0", linewidth=1, label="50d Avg")
    ax2.set_ylabel("Volume", fontsize=11)
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(True, alpha=0.2)

    # ─── Panel 3: Short Score ───
    ax3 = axes[2]
    ss = df["Short_Score"]
    ss_colors = ss.map({1: "#d32f2f", 4: "#e65100", 7: "#ffa000", 11: "#bdbdbd"}).fillna("#bdbdbd")
    ax3.bar(dates, ss, color=ss_colors, alpha=0.7, width=1)
    ax3.axhline(y=7, color="orange", linestyle="--", alpha=0.5, label="Entry threshold (≤7)")
    ax3.set_ylabel("Short Score", fontsize=11)
    ax3.set_ylim(0, 12)
    ax3.legend(loc="upper left", fontsize=9)
    ax3.grid(True, alpha=0.2)

    # Format x-axis
    for a in axes:
        a.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        a.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    path = f".tmp/short_analysis_{symbol}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    return path


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "PAYX"
    path = plot(symbol)
    print(f"Chart saved to {path}")

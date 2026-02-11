"""
Short Portfolio Equity Curve by Market Regime - Nasdaq 100

Simulates a portfolio of short positions using bear_flag + time_stop strategy,
tracking daily equity and classifying market regimes based on % of universe
in Stage 3/4.

Market Regimes:
  - BULL:    <25% of Nasdaq 100 in Stage 3/4
  - NEUTRAL: 25-40% in Stage 3/4
  - BEAR:    >40% in Stage 3/4

Output: Equity curve chart saved to .tmp/short_equity_curve.png

Usage:
    python3 tools/short_equity_curve.py
"""

import os
import sys
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backtest import BacktestEngine
from tools.fetch_index_tickers import fetch_nasdaq100

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Strategy parameters
NUM_SHORT_SLOTS = 10
MAX_TIME_DAYS = 40
REGIME_BULL_THRESHOLD = 0.25      # <25% in Stage 3/4
REGIME_BEAR_THRESHOLD = 0.40      # >40% in Stage 3/4


def load_all_data(tickers: List[str]) -> Dict[str, pd.DataFrame]:
    """Download and prepare 5y data for all tickers."""
    def fetch(t: str) -> Tuple[str, Optional[pd.DataFrame]]:
        try:
            engine = BacktestEngine(t, period="5y")
            engine.prepare_data()
            return t, engine.data
        except Exception:
            return t, None

    logger.info(f"Loading data for {len(tickers)} tickers (10 threads)...")
    cache = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(fetch, tickers))
    for ticker, data in results:
        if data is not None and len(data) > 250:
            cache[ticker] = data
    logger.info(f"Loaded {len(cache)}/{len(tickers)} tickers")
    return cache


def calculate_daily_regime(
    data_cache: Dict[str, pd.DataFrame], date: pd.Timestamp
) -> dict:
    """Calculate market regime for a given date."""
    stages = {"Stage 2": 0, "Stage 3": 0, "Stage 4": 0, "Stage 1 / Neutral": 0}
    total = 0

    for ticker, df in data_cache.items():
        if date in df.index:
            stage = df.loc[date, "Stage_Daily"]
            if stage in stages:
                stages[stage] += 1
            total += 1

    if total == 0:
        return {"regime": "NEUTRAL", "pct_bear": 0, "stages": stages, "total": total}

    pct_bear = (stages["Stage 3"] + stages["Stage 4"]) / total

    if pct_bear >= REGIME_BEAR_THRESHOLD:
        regime = "BEAR"
    elif pct_bear < REGIME_BULL_THRESHOLD:
        regime = "BULL"
    else:
        regime = "NEUTRAL"

    return {"regime": regime, "pct_bear": pct_bear, "stages": stages, "total": total}


def run_short_portfolio(
    data_cache: Dict[str, pd.DataFrame],
    regime_filter: bool = False,
) -> pd.DataFrame:
    """
    Simulate short portfolio with bear_flag + time_stop strategy.
    If regime_filter=True, only open new shorts when market is BEAR (>40% in Stage 3/4).
    Returns DataFrame with daily equity, regime info, and position count.
    """
    # Build unified date index
    all_dates = set()
    for df in data_cache.values():
        all_dates.update(df.index)
    market_index = sorted(all_dates)

    logger.info(f"Simulation period: {market_index[0]} to {market_index[-1]}")
    logger.info(f"Total trading days: {len(market_index)}")

    # State
    equity = 100.0
    short_slots: List[dict] = []  # {"ticker", "entry_price", "entry_date", "days_held"}
    history = []
    trade_log = []

    for i in range(1, len(market_index)):
        date = market_index[i]
        prev_date = market_index[i - 1]

        # 1. Calculate daily P&L for active shorts
        daily_sum_returns = 0.0
        for slot in short_slots:
            ticker = slot["ticker"]
            df = data_cache[ticker]
            if date in df.index and prev_date in df.index:
                # Short P&L: price goes down = profit
                price_change = (
                    df.loc[date, "Close"] - df.loc[prev_date, "Close"]
                ) / df.loc[prev_date, "Close"]
                daily_sum_returns -= price_change  # Negate for shorts

        # Equal weight across all slots (including empty)
        daily_pct = (daily_sum_returns / NUM_SHORT_SLOTS) * 100
        equity *= 1 + daily_pct / 100

        # 2. Market regime
        regime_info = calculate_daily_regime(data_cache, date)

        # 3. Exit management
        for j in reversed(range(len(short_slots))):
            slot = short_slots[j]
            ticker = slot["ticker"]
            df = data_cache[ticker]
            slot["days_held"] += 1

            if date not in df.index:
                # Data gap - close position
                short_slots.pop(j)
                continue

            row = df.loc[date]
            stage = row["Stage_Daily"]
            exit_short = False

            # Stage reversal exit
            if stage in ("Stage 2", "Stage 1 / Neutral"):
                exit_short = True

            # Time stop exit
            if slot["days_held"] >= MAX_TIME_DAYS:
                exit_short = True

            if exit_short:
                exit_price = row["Close"]
                ret = (slot["entry_price"] - exit_price) / slot["entry_price"] * 100
                trade_log.append({
                    "Ticker": ticker,
                    "Entry_Date": slot["entry_date"],
                    "Exit_Date": date,
                    "Entry_Price": slot["entry_price"],
                    "Exit_Price": exit_price,
                    "Return_Pct": ret,
                    "Days_Held": slot["days_held"],
                    "Regime_At_Entry": slot.get("regime_at_entry", "N/A"),
                })
                short_slots.pop(j)

        # 4. Entry: Bear flag - Stage 4, Short_Score ≤ 7
        #    If regime_filter is ON, only enter new shorts in BEAR regime
        if regime_filter and regime_info["regime"] != "BEAR":
            pass  # Skip entries - market not bearish enough
        elif len(short_slots) < NUM_SHORT_SLOTS:
            active_tickers = {s["ticker"] for s in short_slots}
            candidates = []

            for ticker, df in data_cache.items():
                if ticker in active_tickers or date not in df.index:
                    continue
                row = df.loc[date]
                if row["Stage_Daily"] == "Stage 4" and row["Short_Score"] <= 7:
                    candidates.append({
                        "ticker": ticker,
                        "short_score": row["Short_Score"],
                        "close": row["Close"],
                        "atr": row["ATR"],
                    })

            # Rank by Short_Score (lower = better entry)
            candidates.sort(key=lambda x: x["short_score"])

            while len(short_slots) < NUM_SHORT_SLOTS and candidates:
                c = candidates.pop(0)
                short_slots.append({
                    "ticker": c["ticker"],
                    "entry_price": c["close"],
                    "entry_date": date,
                    "days_held": 0,
                    "regime_at_entry": regime_info["regime"],
                })

        # 5. Record daily state
        history.append({
            "Date": date,
            "Equity": equity,
            "Short_Count": len(short_slots),
            "Regime": regime_info["regime"],
            "Pct_Bear": regime_info["pct_bear"],
            "Pct_Stage3": regime_info["stages"].get("Stage 3", 0) / max(regime_info["total"], 1),
            "Pct_Stage4": regime_info["stages"].get("Stage 4", 0) / max(regime_info["total"], 1),
            "Pct_Stage2": regime_info["stages"].get("Stage 2", 0) / max(regime_info["total"], 1),
        })

    result = pd.DataFrame(history)
    result["Date"] = pd.to_datetime(result["Date"], utc=True)
    result = result.set_index("Date")

    trades_df = pd.DataFrame(trade_log) if trade_log else pd.DataFrame()

    logger.info(f"Simulation complete. Final equity: {equity:.2f}")
    logger.info(f"Total trades: {len(trade_log)}")

    return result, trades_df


def calculate_regime_metrics(result: pd.DataFrame, trades_df: pd.DataFrame) -> pd.DataFrame:
    """Calculate performance metrics broken down by market regime."""
    metrics = []

    for regime in ["BULL", "NEUTRAL", "BEAR"]:
        # Daily returns in this regime
        regime_days = result[result["Regime"] == regime]
        if len(regime_days) == 0:
            continue

        daily_ret = regime_days["Equity"].pct_change().dropna()

        # Trades entered in this regime
        if not trades_df.empty and "Regime_At_Entry" in trades_df.columns:
            regime_trades = trades_df[trades_df["Regime_At_Entry"] == regime]
        else:
            regime_trades = pd.DataFrame()

        # Metrics
        total_days = len(regime_days)
        pct_days = total_days / len(result) * 100

        if len(regime_trades) > 0:
            win_rate = (regime_trades["Return_Pct"] > 0).mean() * 100
            avg_ret = regime_trades["Return_Pct"].mean()
            median_ret = regime_trades["Return_Pct"].median()
            gross_w = regime_trades.loc[regime_trades["Return_Pct"] > 0, "Return_Pct"].sum()
            gross_l = abs(regime_trades.loc[regime_trades["Return_Pct"] <= 0, "Return_Pct"].sum())
            pf = round(gross_w / gross_l, 2) if gross_l > 0 else 99.0
            num_trades = len(regime_trades)
        else:
            win_rate = avg_ret = median_ret = pf = 0
            num_trades = 0

        # Annualized return in this regime
        if len(daily_ret) > 1:
            cum_ret = (1 + daily_ret).prod() - 1
            ann_vol = daily_ret.std() * np.sqrt(252) * 100
        else:
            cum_ret = 0
            ann_vol = 0

        # Max drawdown in regime
        eq = regime_days["Equity"]
        rolling_max = eq.cummax()
        drawdown = (eq - rolling_max) / rolling_max
        max_dd = drawdown.min() * 100

        avg_short_count = regime_days["Short_Count"].mean()

        metrics.append({
            "Regime": regime,
            "Days": total_days,
            "% Time": round(pct_days, 1),
            "Trades": num_trades,
            "Win_%": round(win_rate, 1),
            "Avg_Ret_%": round(avg_ret, 2),
            "Median_%": round(median_ret, 2),
            "PF": pf,
            "Regime_Return_%": round(cum_ret * 100, 2),
            "Max_DD_%": round(max_dd, 2),
            "Ann_Vol_%": round(ann_vol, 2),
            "Avg_Shorts": round(avg_short_count, 1),
        })

    return pd.DataFrame(metrics)


def plot_comparison(
    result_raw: pd.DataFrame,
    result_filtered: pd.DataFrame,
    trades_raw: pd.DataFrame,
    trades_filtered: pd.DataFrame,
):
    """Generate comparison chart: always-on vs regime-filtered."""
    fig, axes = plt.subplots(3, 1, figsize=(18, 14), gridspec_kw={"height_ratios": [3, 1, 1]})
    fig.suptitle(
        "Short Portfolio Comparison: Always-On vs Regime Filter (>40% Bear)\n"
        "Bear Flag + Time Stop (40d) — Nasdaq 100 (5 Years)",
        fontsize=15, fontweight="bold", y=0.99,
    )

    dates_raw = result_raw.index
    dates_filt = result_filtered.index

    # --- Panel 1: Both equity curves + regime shading ---
    ax1 = axes[0]

    # Shade regimes (use raw result for regime data)
    regime_colors = {"BULL": "#e8f5e9", "NEUTRAL": "#fff9c4", "BEAR": "#ffebee"}
    prev_regime = result_raw["Regime"].iloc[0]
    regime_start = dates_raw[0]
    for i in range(1, len(result_raw)):
        curr_regime = result_raw["Regime"].iloc[i]
        if curr_regime != prev_regime or i == len(result_raw) - 1:
            ax1.axvspan(regime_start, dates_raw[i], alpha=0.35,
                        color=regime_colors.get(prev_regime, "#f5f5f5"), linewidth=0)
            regime_start = dates_raw[i]
            prev_regime = curr_regime

    # Plot both curves
    ret_raw = (result_raw["Equity"].iloc[-1] / 100 - 1) * 100
    ret_filt = (result_filtered["Equity"].iloc[-1] / 100 - 1) * 100

    ax1.plot(dates_raw, result_raw["Equity"], color="#ef5350", linewidth=1.5,
             alpha=0.7, linestyle="--", label=f"Always-On ({ret_raw:+.1f}%)")
    ax1.plot(dates_filt, result_filtered["Equity"], color="#1565c0", linewidth=2.5,
             label=f"Regime Filter ({ret_filt:+.1f}%)")
    ax1.axhline(y=100, color="gray", linestyle=":", alpha=0.4)

    ax1.set_ylabel("Equity (Start = 100)", fontsize=12)
    ax1.grid(True, alpha=0.2)

    # Legends
    regime_patches = [
        Patch(facecolor="#e8f5e9", alpha=0.5, label="BULL (<25%)"),
        Patch(facecolor="#fff9c4", alpha=0.5, label="NEUTRAL (25-40%)"),
        Patch(facecolor="#ffebee", alpha=0.5, label="BEAR (>40%)"),
    ]
    legend1 = ax1.legend(handles=regime_patches, loc="lower left", fontsize=9, ncol=3)
    ax1.add_artist(legend1)
    ax1.legend(loc="upper right", fontsize=11)

    # Annotate final values
    for res, color, ha, offset in [
        (result_raw, "#ef5350", "right", -0.02),
        (result_filtered, "#1565c0", "right", 0.02),
    ]:
        final = res["Equity"].iloc[-1]
        ret = (final / 100 - 1) * 100
        ax1.annotate(
            f"{final:.1f} ({ret:+.1f}%)", xy=(res.index[-1], final),
            fontsize=10, fontweight="bold", color=color, ha=ha,
        )

    # --- Panel 2: % of universe in Stage 3/4 ---
    ax2 = axes[1]
    pct_bear = result_raw["Pct_Bear"] * 100
    ax2.fill_between(dates_raw, pct_bear, alpha=0.5, color="#ef5350", label="% Stage 3+4")
    ax2.axhline(y=40, color="#d32f2f", linestyle="--", linewidth=2, alpha=0.8,
                label="BEAR threshold (40%) — Shorts ON")
    ax2.axhline(y=25, color="#2e7d32", linestyle="--", linewidth=1, alpha=0.6,
                label="BULL threshold (25%)")
    ax2.set_ylabel("% NDX in Stage 3/4", fontsize=11)
    ax2.set_ylim(0, 80)
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(True, alpha=0.2)

    # --- Panel 3: Active positions comparison ---
    ax3 = axes[2]
    ax3.fill_between(dates_raw, result_raw["Short_Count"], alpha=0.3,
                     color="#ef5350", label="Always-On shorts")
    ax3.fill_between(dates_filt, result_filtered["Short_Count"], alpha=0.6,
                     color="#1565c0", label="Regime Filter shorts")
    ax3.axhline(y=NUM_SHORT_SLOTS, color="gray", linestyle=":", alpha=0.4)
    ax3.set_ylabel("# Short Positions", fontsize=11)
    ax3.set_ylim(0, NUM_SHORT_SLOTS + 2)
    ax3.legend(loc="upper left", fontsize=9)
    ax3.grid(True, alpha=0.2)
    ax3.set_xlabel("Date", fontsize=12)

    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    path = ".tmp/short_equity_comparison.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    logger.info(f"Comparison chart saved to {path}")
    plt.close()
    return path


def get_portfolio_stats(result: pd.DataFrame, trades_df: pd.DataFrame, label: str) -> dict:
    """Calculate summary stats for a portfolio run."""
    final_eq = result["Equity"].iloc[-1]
    total_ret = (final_eq / 100 - 1) * 100
    daily_ret = result["Equity"].pct_change().dropna()
    ann_vol = daily_ret.std() * np.sqrt(252) * 100
    num_years = (result.index[-1] - result.index[0]).days / 365.25
    cagr = ((final_eq / 100) ** (1 / num_years) - 1) * 100
    rolling_max = result["Equity"].cummax()
    max_dd = ((result["Equity"] - rolling_max) / rolling_max).min() * 100
    sharpe = cagr / ann_vol if ann_vol > 0 else 0

    n_trades = len(trades_df) if not trades_df.empty else 0
    win_rate = (trades_df["Return_Pct"] > 0).mean() * 100 if n_trades > 0 else 0
    avg_trade = trades_df["Return_Pct"].mean() if n_trades > 0 else 0

    gross_w = trades_df.loc[trades_df["Return_Pct"] > 0, "Return_Pct"].sum() if n_trades > 0 else 0
    gross_l = abs(trades_df.loc[trades_df["Return_Pct"] <= 0, "Return_Pct"].sum()) if n_trades > 0 else 0.01
    pf = round(gross_w / gross_l, 2) if gross_l > 0 else 0

    return {
        "Version": label,
        "Total_Return_%": round(total_ret, 2),
        "CAGR_%": round(cagr, 2),
        "Ann_Vol_%": round(ann_vol, 2),
        "Max_DD_%": round(max_dd, 2),
        "Sharpe": round(sharpe, 2),
        "Trades": n_trades,
        "Win_%": round(win_rate, 1),
        "Avg_Trade_%": round(avg_trade, 2),
        "Profit_Factor": pf,
    }


def main():
    os.makedirs(".tmp", exist_ok=True)

    # 1. Fetch tickers
    logger.info("Fetching Nasdaq 100 tickers...")
    ndx = fetch_nasdaq100()
    tickers = ndx["Ticker"].tolist()

    # 2. Load all data (once, shared by both runs)
    data_cache = load_all_data(tickers)

    # 3a. Run WITHOUT regime filter (always-on)
    logger.info("Running short portfolio: ALWAYS-ON...")
    result_raw, trades_raw = run_short_portfolio(data_cache, regime_filter=False)

    # 3b. Run WITH regime filter (only short in BEAR)
    logger.info("Running short portfolio: REGIME FILTER (>40% bear)...")
    result_filt, trades_filt = run_short_portfolio(data_cache, regime_filter=True)

    # 4. Calculate stats
    stats_raw = get_portfolio_stats(result_raw, trades_raw, "Always-On")
    stats_filt = get_portfolio_stats(result_filt, trades_filt, "Regime Filter")
    comparison_df = pd.DataFrame([stats_raw, stats_filt])

    # 5. Print comparison
    print("\n" + "=" * 95)
    print("SHORT PORTFOLIO COMPARISON: Always-On vs Regime Filter")
    print(f"Strategy: Bear Flag + Time Stop (40d) | Universe: Nasdaq 100 | Slots: {NUM_SHORT_SLOTS}")
    print("=" * 95)
    print()
    print(comparison_df.to_string(index=False))
    print()

    # Improvement
    delta_ret = stats_filt["Total_Return_%"] - stats_raw["Total_Return_%"]
    delta_dd = stats_filt["Max_DD_%"] - stats_raw["Max_DD_%"]
    print(f"  Regime Filter improvement:")
    print(f"    Return: {delta_ret:+.2f}% better")
    print(f"    Max DD: {delta_dd:+.2f}% (less drawdown = better)")
    print(f"    Trades: {stats_filt['Trades']} vs {stats_raw['Trades']} ({stats_filt['Trades'] - stats_raw['Trades']:+d})")
    print(f"    PF: {stats_filt['Profit_Factor']} vs {stats_raw['Profit_Factor']}")
    print("=" * 95)

    # 6. Generate comparison chart
    chart_path = plot_comparison(result_raw, result_filt, trades_raw, trades_filt)

    print(f"\nChart saved: {chart_path}")

    # 7. Save data
    result_filt.to_csv(".tmp/short_equity_filtered.csv")
    comparison_df.to_csv(".tmp/short_comparison_stats.csv", index=False)
    if not trades_filt.empty:
        trades_filt.to_csv(".tmp/short_portfolio_trades_filtered.csv", index=False)
    logger.info("All data saved to .tmp/")


if __name__ == "__main__":
    main()

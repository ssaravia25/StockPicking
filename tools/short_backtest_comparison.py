"""
Short Strategy Backtest Comparison - Nasdaq 100

Tests 6 short entry strategies × 3 exit variants across the Nasdaq 100 universe.
Uses 5 years of historical data to find the most reliable short-selling approach.

Entry Strategies:
  1. distribution_trap     - Stage 2→3, Short_Score ≤ 7
  2. stage3_breakdown      - Stage 3→4 transition
  3. bear_flag             - In Stage 4, Short_Score ≤ 7
  4. aggressive_distribution - Stage 2→3 (no score filter)
  5. tight_bear_flag       - In Stage 4, Short_Score ≤ 4
  6. weak_bounce           - In Stage 3, Short_Score ≥7→≤4

Exit Variants:
  A. stage_reversal - Exit when stage returns to Stage 2 or Stage 1
  B. atr_stop       - A + stop loss at entry + 2×ATR
  C. time_stop      - A + max 40 trading days

Usage:
    python3 tools/short_backtest_comparison.py
"""

import os
import sys
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backtest import BacktestEngine
from tools.fetch_index_tickers import fetch_nasdaq100

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Strategy definitions
# -------------------------------------------------------------------

STRATEGIES = [
    "distribution_trap",
    "stage3_breakdown",
    "bear_flag",
    "aggressive_distribution",
    "tight_bear_flag",
    "weak_bounce",
]

EXIT_VARIANTS = ["stage_reversal", "atr_stop", "time_stop"]

MAX_TIME_DAYS = 40  # For time_stop exit


def run_short_strategy(
    df: pd.DataFrame,
    strategy: str,
    exit_variant: str,
) -> List[dict]:
    """
    Run a single short strategy on a prepared DataFrame.
    Returns list of trade dicts.
    """
    df = df.copy()
    df["Prev_Stage"] = df["Stage_Daily"].shift(1)
    df["Prev_Short_Score"] = df["Short_Score"].shift(1)

    position = 0  # -1 = short, 0 = cash
    trades = []
    entry_price = 0.0
    entry_date = None
    entry_atr = 0.0
    days_held = 0

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        date = df.index[i]
        close = row["Close"]
        stage = row["Stage_Daily"]
        prev_stage = prev["Stage_Daily"]
        short_score = row["Short_Score"]
        prev_short_score = prev["Short_Score"]

        # ---- ENTRY ----
        if position == 0:
            enter = False

            if strategy == "distribution_trap":
                enter = prev_stage == "Stage 2" and stage == "Stage 3" and short_score <= 7

            elif strategy == "stage3_breakdown":
                enter = prev_stage == "Stage 3" and stage == "Stage 4"

            elif strategy == "bear_flag":
                enter = stage == "Stage 4" and short_score <= 7

            elif strategy == "aggressive_distribution":
                enter = prev_stage == "Stage 2" and stage == "Stage 3"

            elif strategy == "tight_bear_flag":
                enter = stage == "Stage 4" and short_score <= 4

            elif strategy == "weak_bounce":
                enter = (
                    stage == "Stage 3"
                    and prev_short_score >= 7
                    and short_score <= 4
                )

            if enter:
                position = -1
                entry_price = close
                entry_date = date
                entry_atr = row["ATR"]
                days_held = 0

        # ---- EXIT ----
        elif position == -1:
            days_held += 1
            exit_short = False

            # A: stage reversal (common to all)
            if stage in ("Stage 2", "Stage 1 / Neutral"):
                exit_short = True

            # B: ATR stop loss
            if exit_variant == "atr_stop" and not exit_short:
                stop_price = entry_price + 2 * entry_atr
                if close >= stop_price:
                    exit_short = True

            # C: time stop
            if exit_variant == "time_stop" and not exit_short:
                if days_held >= MAX_TIME_DAYS:
                    exit_short = True

            if exit_short:
                trades.append(
                    {
                        "Entry_Date": entry_date,
                        "Exit_Date": date,
                        "Entry_Price": entry_price,
                        "Exit_Price": close,
                        "Return_Pct": (entry_price - close) / entry_price * 100,
                        "Days_Held": days_held,
                    }
                )
                position = 0

    # Close open position at end
    if position == -1:
        last = df.iloc[-1]
        trades.append(
            {
                "Entry_Date": entry_date,
                "Exit_Date": df.index[-1],
                "Entry_Price": entry_price,
                "Exit_Price": last["Close"],
                "Return_Pct": (entry_price - last["Close"]) / entry_price * 100,
                "Days_Held": days_held,
            }
        )

    return trades


# -------------------------------------------------------------------
# Data loading (parallel)
# -------------------------------------------------------------------


def load_all_data(tickers: List[str]) -> Dict[str, pd.DataFrame]:
    """Download and prepare data for all tickers using thread pool."""

    def fetch(t: str) -> Tuple[str, Optional[pd.DataFrame]]:
        try:
            engine = BacktestEngine(t, period="5y")
            engine.prepare_data()
            return t, engine.data
        except Exception:
            return t, None

    logger.info(f"Loading data for {len(tickers)} tickers (10 threads)...")
    cache: Dict[str, pd.DataFrame] = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(fetch, tickers))

    for ticker, data in results:
        if data is not None and len(data) > 250:
            cache[ticker] = data

    logger.info(f"Loaded {len(cache)}/{len(tickers)} tickers successfully")
    return cache


# -------------------------------------------------------------------
# Metrics
# -------------------------------------------------------------------


def calculate_metrics(trades: List[dict], strategy: str, exit_variant: str) -> dict:
    """Calculate aggregate metrics from a list of trades."""
    if not trades:
        return {
            "Strategy": strategy,
            "Exit": exit_variant,
            "Trades": 0,
            "Win_%": 0,
            "Avg_%": 0,
            "Median_%": 0,
            "Profit_Factor": 0,
            "Best_%": 0,
            "Worst_%": 0,
            "Avg_Days": 0,
            "Max_Consec_Loss": 0,
            "Tickers_Active": 0,
        }

    df = pd.DataFrame(trades)
    returns = df["Return_Pct"]

    wins = returns[returns > 0]
    losses = returns[returns <= 0]

    # Profit factor
    gross_wins = wins.sum() if len(wins) > 0 else 0
    gross_losses = abs(losses.sum()) if len(losses) > 0 else 0.01
    profit_factor = round(gross_wins / gross_losses, 2)

    # Max consecutive losses
    max_consec = 0
    current_consec = 0
    for r in returns:
        if r <= 0:
            current_consec += 1
            max_consec = max(max_consec, current_consec)
        else:
            current_consec = 0

    # Unique tickers that generated trades
    tickers_active = len(set(t.get("Ticker", "") for t in trades))

    return {
        "Strategy": strategy,
        "Exit": exit_variant,
        "Trades": len(df),
        "Win_%": round((returns > 0).mean() * 100, 1),
        "Avg_%": round(returns.mean(), 2),
        "Median_%": round(returns.median(), 2),
        "Profit_Factor": profit_factor,
        "Best_%": round(returns.max(), 2),
        "Worst_%": round(returns.min(), 2),
        "Avg_Days": round(df["Days_Held"].mean(), 1),
        "Max_Consec_Loss": max_consec,
        "Tickers_Active": tickers_active,
    }


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------


def main():
    os.makedirs(".tmp", exist_ok=True)

    # 1. Fetch tickers
    logger.info("Fetching Nasdaq 100 tickers...")
    ndx = fetch_nasdaq100()
    tickers = ndx["Ticker"].tolist()

    # 2. Load data (once, reuse for all strategies)
    data_cache = load_all_data(tickers)

    # 3. Run all strategy × exit combinations
    all_metrics = []
    all_trades = []

    total_combos = len(STRATEGIES) * len(EXIT_VARIANTS)
    combo_num = 0

    for strategy in STRATEGIES:
        for exit_variant in EXIT_VARIANTS:
            combo_num += 1
            logger.info(
                f"[{combo_num}/{total_combos}] {strategy} + {exit_variant}..."
            )

            combo_trades = []
            for ticker, df in data_cache.items():
                trades = run_short_strategy(df, strategy, exit_variant)
                for t in trades:
                    t["Ticker"] = ticker
                    t["Strategy"] = strategy
                    t["Exit"] = exit_variant
                combo_trades.extend(trades)

            metrics = calculate_metrics(combo_trades, strategy, exit_variant)
            all_metrics.append(metrics)
            all_trades.extend(combo_trades)

    # 4. Build comparison table
    comparison = pd.DataFrame(all_metrics)

    # Sort by Profit Factor descending (best strategy first)
    comparison = comparison.sort_values("Profit_Factor", ascending=False)

    # 5. Print results
    print("\n" + "=" * 100)
    print("SHORT STRATEGY BACKTEST COMPARISON - NASDAQ 100 (5 Years)")
    print("=" * 100)
    print(f"\nUniverse: {len(data_cache)} tickers | Period: 5 years\n")
    print(comparison.to_string(index=False))
    print("=" * 100)

    # Highlight winner
    best = comparison.iloc[0]
    print(f"\nBEST STRATEGY: {best['Strategy']} + {best['Exit']}")
    print(f"  Trades: {best['Trades']}  |  Win Rate: {best['Win_%']}%  |  Avg Return: {best['Avg_%']}%")
    print(f"  Profit Factor: {best['Profit_Factor']}  |  Avg Days: {best['Avg_Days']}  |  Max Consec Loss: {best['Max_Consec_Loss']}")

    # Top 3
    print("\n--- Top 3 Strategies ---")
    for i, (_, row) in enumerate(comparison.head(3).iterrows()):
        print(f"  #{i+1}: {row['Strategy']} + {row['Exit']}  "
              f"PF={row['Profit_Factor']}  Win={row['Win_%']}%  Avg={row['Avg_%']}%  "
              f"Trades={row['Trades']}  Days={row['Avg_Days']}")

    # 6. Save outputs
    comparison.to_csv(".tmp/short_strategy_comparison.csv", index=False)
    logger.info("Saved comparison to .tmp/short_strategy_comparison.csv")

    trades_df = pd.DataFrame(all_trades)
    if not trades_df.empty:
        trades_df.to_csv(".tmp/short_all_trades.csv", index=False)
        logger.info(f"Saved {len(trades_df)} trades to .tmp/short_all_trades.csv")

    # 7. Per-strategy breakdown by stage
    if not trades_df.empty:
        print("\n--- Trade Distribution by Stage at Entry ---")
        for strategy in STRATEGIES:
            strat_trades = trades_df[
                (trades_df["Strategy"] == strategy)
                & (trades_df["Exit"] == "stage_reversal")
            ]
            if len(strat_trades) > 0:
                win_rate = (strat_trades["Return_Pct"] > 0).mean() * 100
                print(f"  {strategy:30s}  trades={len(strat_trades):4d}  "
                      f"win={win_rate:5.1f}%  avg={strat_trades['Return_Pct'].mean():+6.2f}%")


if __name__ == "__main__":
    main()

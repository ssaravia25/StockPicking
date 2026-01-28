"""
Portfolio Backtester and Equity Curve Generator.
Mixes Long and Short strategies and calculates an aggregated equity curve.
"""

import logging
import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt
from backtest import BacktestEngine

# Tickers
longs = ["NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "NFLX"]
shorts = ["INTC", "PYPL", "SNOW", "DIS", "BABA", "PFE", "MMM", "CVS", "SNAP", "AMD"]

def get_daily_equity(results, start_date, end_date):
    """
    Simulates daily account value for a specific ticker's trades.
    Assumes fixed fractional sizing (each trade is 1/N of the starting equity).
    """
    # Create an empty series for all days
    # Ensure index is timezone-unaware for comparison
    date_range = pd.date_range(start=start_date, end=end_date).tz_localize(None)
    daily_returns = pd.Series(0.0, index=date_range)
    
    if results is None or "Trade_Log" not in results:
        return daily_returns
    
    trade_log = results["Trade_Log"].copy()
    # Ensure trade dates are timezone-unaware
    trade_log["Entry_Date"] = pd.to_datetime(trade_log["Entry_Date"]).dt.tz_localize(None)
    trade_log["Exit_Date"] = pd.to_datetime(trade_log["Exit_Date"]).dt.tz_localize(None)
    
    for _, trade in trade_log.iterrows():
        # Distribute the total Return_Pct linearly over the trade duration
        # (This is a simplification; real daily equity would use actual close prices)
        duration = (trade["Exit_Date"] - trade["Entry_Date"]).days + 1
        return_per_day = trade["Return_Pct"] / duration
        
        mask = (daily_returns.index >= trade["Entry_Date"]) & (daily_returns.index <= trade["Exit_Date"])
        daily_returns[mask] += return_per_day
    
    return daily_returns

def run_portfolio():
    print("Starting Portfolio Backtest...")
    
    start_date = "2021-01-01"
    end_date = "2026-01-01"
    
    long_equities = []
    short_equities = []
    
    print("\n--- Processing Longs (Swing Pullback) ---")
    for t in longs:
        try:
            print(f"Backtesting {t}...", end=" ", flush=True)
            engine = BacktestEngine(t, period="5y")
            engine.run_strategy(strategy_type="swing_pullback")
            equity = get_daily_equity(engine.results, start_date, end_date)
            long_equities.append(equity)
            print("Done.")
        except Exception as e:
            print(f"Error: {e}")
            
    print("\n--- Processing Shorts (Distribution Trap) ---")
    for t in shorts:
        try:
            print(f"Backtesting {t}...", end=" ", flush=True)
            engine = BacktestEngine(t, period="5y")
            engine.run_strategy(strategy_type="distribution_trap")
            equity = get_daily_equity(engine.results, start_date, end_date)
            short_equities.append(equity)
            print("Done.")
        except Exception as e:
            print(f"Error: {e}")

    # Aggregate
    avg_long_daily = pd.concat(long_equities, axis=1).mean(axis=1) if long_equities else pd.Series(0.0)
    avg_short_daily = pd.concat(short_equities, axis=1).mean(axis=1) if short_equities else pd.Series(0.0)
    
    # Portfolio is the average of all 20 active "slots" (10 Long, 10 Short)
    combined_daily = (avg_long_daily + avg_short_daily) / 2
    
    # Download SPY Benchmark
    print("\n--- Processing Benchmark (SPY) ---")
    spy = yf.Ticker("SPY").history(start=start_date, end=end_date)
    spy.index = spy.index.tz_localize(None)
    spy_daily_returns = spy["Close"].pct_change().fillna(0) * 100
    
    # Ensure spy alignes with the date range
    spy_daily_returns = spy_daily_returns.reindex(pd.date_range(start=start_date, end=end_date)).fillna(0)

    # Compound Returns
    long_curve = (1 + avg_long_daily/100).cumprod() * 100
    short_curve = (1 + avg_short_daily/100).cumprod() * 100
    total_curve = (1 + combined_daily/100).cumprod() * 100
    spy_curve = (1 + spy_daily_returns/100).cumprod() * 100
    
    # Metrics
    def calc_vol(daily_ret_series):
        return daily_ret_series.std() * np.sqrt(252) # Annualized Vol

    def calc_sharpe(daily_ret_series):
        vol = calc_vol(daily_ret_series)
        if vol == 0: return 0
        return (daily_ret_series.mean() * 252) / vol # Annualized Sharpe (RF=0)

    stats = {
        "Longs": {"Return": long_curve.iloc[-1] - 100, "Vol": calc_vol(avg_long_daily), "Sharpe": calc_sharpe(avg_long_daily)},
        "Shorts": {"Return": short_curve.iloc[-1] - 100, "Vol": calc_vol(avg_short_daily), "Sharpe": calc_sharpe(avg_short_daily)},
        "Portfolio": {"Return": total_curve.iloc[-1] - 100, "Vol": calc_vol(combined_daily), "Sharpe": calc_sharpe(combined_daily)},
        "SPY": {"Return": spy_curve.iloc[-1] - 100, "Vol": calc_vol(spy_daily_returns), "Sharpe": calc_sharpe(spy_daily_returns)}
    }

    # Plot
    plt.figure(figsize=(14, 7))
    plt.plot(long_curve, label=f"Longs (Return: {stats['Longs']['Return']:.1f}%)", color="green", alpha=0.5)
    plt.plot(short_curve, label=f"Shorts (Return: {stats['Shorts']['Return']:.1f}%)", color="red", alpha=0.5)
    plt.plot(spy_curve, label=f"SPY (Return: {stats['SPY']['Return']:.1f}%)", color="gray", linestyle="--", alpha=0.8)
    plt.plot(total_curve, label=f"Total Portfolio (Return: {stats['Portfolio']['Return']:.1f}%)", color="blue", linewidth=3)
    
    plt.title("Portfolio Performance vs SPY Benchmark (2021-2026)")
    plt.ylabel("Equity Value (Starting at 100)")
    plt.grid(True, which='both', linestyle='--', alpha=0.5)
    plt.legend()
    
    plot_path = "portfolio_equity.png"
    plt.savefig(plot_path)
    print(f"\nPlot saved to {plot_path}")
    
    # Final Stats
    print("\n" + "="*70)
    print(f"{'STRATEGY':<15} | {'RETURN %':<10} | {'VOL % (Ann)':<12} | {'SHARPE':<10}")
    print("-" * 70)
    for k, v in stats.items():
        print(f"{k:<15} | {v['Return']:>10.2f} | {v['Vol']:>12.2f} | {v['Sharpe']:>10.2f}")
    print("="*70)

if __name__ == "__main__":
    run_portfolio()


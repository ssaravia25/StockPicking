import pandas as pd
import matplotlib.pyplot as plt
from ranking_backtest import RankingEngine, universe, get_stats
import logging

def compare_slots():
    logging.basicConfig(level=logging.WARNING)
    
    print("Running 10-Slot Simulation...")
    engine_10 = RankingEngine(universe, num_slots=10)
    res_10, trades_10 = engine_10.run(exit_strategy="trend_guardian")
    stats_10 = get_stats(res_10)
    
    print("Running 20-Slot Simulation...")
    engine_20 = RankingEngine(universe, num_slots=20)
    res_20, trades_20 = engine_20.run(exit_strategy="trend_guardian")
    stats_20 = get_stats(res_20)
    
    print("\n" + "="*80)
    print(f"{'METRIC':<20} | {'10 SLOTS':<25} | {'20 SLOTS':<25}")
    print("-" * 80)
    print(f"{'Total Return':<20} | {stats_10[0]:>22.2f}% | {stats_20[0]:>22.2f}%")
    print(f"{'CAGR':<20} | {stats_10[3]:>22.2f}% | {stats_20[3]:>22.2f}%")
    print(f"{'Ann. Volatility':<20} | {stats_10[1]:>22.2f}% | {stats_20[1]:>22.2f}%")
    print(f"{'Max Drawdown':<20} | {stats_10[2]:>22.2f}% | {stats_20[2]:>22.2f}%")
    print(f"{'Sharpe (CAGR/Vol)':<20} | {stats_10[3]/stats_10[1]:>22.2f} | {stats_20[3]/stats_20[1]:>22.2f}")
    print("="*80)
    
    # Check AMD capture in 20-slot
    amd_20 = trades_20[trades_20["Ticker"] == "AMD"]
    print("\nAMD Trades in 20-Slot Portfolio (2024+):")
    print(amd_20[amd_20["Entry_Date"] >= "2024-01-01"])

    plt.figure(figsize=(14, 7))
    plt.plot(res_10["Equity"], label=f"10 Slots (+{stats_10[0]:.0f}%)", color="blue")
    plt.plot(res_20["Equity"], label=f"20 Slots (+{stats_20[0]:.0f}%)", color="green")
    plt.title("Portfolio Capacity Comparison: 10 vs 20 Slots")
    plt.ylabel("Equity")
    plt.legend()
    plt.grid(True)
    plt.savefig("slot_comparison.png")
    print("\nPlot saved to slot_comparison.png")

if __name__ == "__main__":
    compare_slots()

import pandas as pd
from ranking_backtest import RankingEngine, universe

def check_slots_and_ranking():
    engine = RankingEngine(universe)
    res_equity, res_trades = engine.run(exit_strategy="trend_guardian")
    
    # Check Jan 2024 period
    start = pd.Timestamp("2024-01-01", tz="UTC")
    end = pd.Timestamp("2024-02-01", tz="UTC")
    
    # Equity history (which includes Long_Count)
    history = res_equity.loc[start:end]
    print("--- Portfolio Occupancy (Jan 2024) ---")
    print(history[["Long_Count"]].head(20))
    
    # Check if AMD was in the trade log at any point in 2024
    amd_trades = res_trades[res_trades["Ticker"] == "AMD"]
    print("\n--- AMD Trade Log (2024+) ---")
    print(amd_trades[amd_trades["Entry_Date"] >= "2024-01-01"])

if __name__ == "__main__":
    check_slots_and_ranking()

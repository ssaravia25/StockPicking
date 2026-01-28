import pandas as pd
import numpy as np
from ranking_backtest import RankingEngine, universe
import logging

def debug_amd():
    engine = RankingEngine(["AMD"])
    engine.load_data()
    df = engine.data_cache["AMD"]
    
    # Filter for 2021-2026
    df = df.loc["2021-01-01":]
    
    # Let's look at some key periods in the user's screenshot:
    # 1. Late 2021 Rally
    # 2. Late 2023 / Early 2024 Rally
    
    # Find potential entry windows: Stage 2 + Trend Template
    potential_entries = df[(df["Stage_Daily"] == "Stage 2") & (df["Trend_Template"] == True)]
    
    print("Potential Windows (Stage 2 + Trend Template):")
    if potential_entries.empty:
        print("NONE found in 2021-2026")
    else:
        # Check minimum Score in these windows
        print(f"Found {len(potential_entries)} days in Stage 2 + Trend Template.")
        print(f"Minimum Score achieved: {potential_entries['Score'].min():.2f}")
        
        # Look at specific rallies
        print("\n--- Late 2021 Sample ---")
        print(df.loc["2021-10-01":"2021-12-31", ["Close", "Stage_Daily", "Score", "Trend_Template"]].head(20))
        
        print("\n--- Late 2023 Sample ---")
        print(df.loc["2023-11-01":"2023-12-31", ["Close", "Stage_Daily", "Score", "Trend_Template"]].head(20))

        print("\n--- Early 2024 Sample ---")
        print(df.loc["2024-01-01":"2024-02-28", ["Close", "Stage_Daily", "Score", "Trend_Template"]].head(20))

if __name__ == "__main__":
    debug_amd()

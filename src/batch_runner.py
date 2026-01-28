"""
Batch Backtest Runner.
Executes strategy backtests for a list of tickers and summarizes the results.
"""

import logging
from backtest import BacktestEngine
import pandas as pd

# Configure logging
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

long_tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "AVGO", "COST", "LLY", "NFLX"]
short_tickers = ["INTC", "WBA", "MMM", "PFE", "CVS", "SNOW", "U", "RIVN", "DIS", "BABA"]

def run_batch(tickers, label):
    print(f"\n--- Running {label} Batch ---")
    summaries = []
    
    for ticker in tickers:
        try:
            print(f"Testing {ticker}...", end=" ", flush=True)
            engine = BacktestEngine(ticker)
            engine.run_strategy()
            res = engine.results
            if res:
                summaries.append({
                    "Ticker": ticker,
                    "Type": label,
                    "Trades": res["Total_Trades"],
                    "WinRate%": res["Win_Rate_Pct"],
                    "StrategyReturn%": res["Strategy_Return_Pct"],
                    "B&H_Return%": res["Buy_Hold_Return_Pct"]
                })
                print("Done.")
            else:
                print("No results.")
        except Exception as e:
            print(f"Error: {e}")
            
    return summaries

if __name__ == "__main__":
    long_results = run_batch(long_tickers, "Long-Biased")
    short_results = run_batch(short_tickers, "Short-Biased")
    
    all_results = long_results + short_results
    df = pd.DataFrame(all_results)
    
    print("\n" + "="*50)
    print("BATCH BACKTEST SUMMARY")
    print("="*50)
    print(df.to_string(index=False))
    
    # Save to CSV for reference
    df.to_csv("batch_results.csv", index=False)
    print("\nResults saved to batch_results.csv")

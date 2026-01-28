
import pandas as pd
from ranking_backtest import RankingEngine, universe

engine = RankingEngine(universe)
engine.load_data()

# Run backtest with trend_guardian (Strict)
res, trades = engine.run(exit_strategy="trend_guardian")

print("\n--- Backtest Results ---")
print(f"Total Equity Start: {res['Equity'].iloc[0]}")
print(f"Total Equity End: {res['Equity'].iloc[-1]}")
print(f"Total Return: {((res['Equity'].iloc[-1] / res['Equity'].iloc[0]) - 1) * 100:.2f}%")

# Annual Breakdown
res.index = pd.to_datetime(res.index)
annual = res["Equity"].resample('YE').last()
print("\n--- Annual Equity Values ---")
print(annual)

years = []
returns = []
first_val = res["Equity"].iloc[0]
years.append(annual.index[0].year)
returns.append(((annual.iloc[0] / first_val) - 1) * 100)

for i in range(1, len(annual)):
    years.append(annual.index[i].year)
    ret = ((annual.iloc[i] / annual.iloc[i-1]) - 1) * 100
    returns.append(ret)

print("\n--- Annual Returns (%) ---")
for y, r in zip(years, returns):
    print(f"{y}: {r:.2f}%")

# Check Tickers in 2025
print("\n--- Monthly Equity & Exposure in 2025 ---")
res_2025 = res[res.index.year == 2025]
monthly_res = res_2025.resample('ME').agg({"Equity": "last", "Long_Count": "mean"})
print(monthly_res)

# Trade log for 2025
print("\n--- Completed Trades in 2025 ---")
trades["Exit_Date"] = pd.to_datetime(trades["Exit_Date"])
completed_2025 = trades[trades["Exit_Date"].dt.year == 2025]
print(completed_2025[["Ticker", "Entry_Date", "Exit_Date", "Return (%)"]])

# Check AAPL trades
print("\n--- Investigating AAPL ---")
aapl_trades = engine.get_ticker_trades("AAPL", exit_strategy="trend_guardian")
print(f"AAPL Trades Found: {len(aapl_trades)}")

if "AAPL" in engine.data_cache:
    df = engine.data_cache["AAPL"]
    latest = df.iloc[-1]
    print(f"AAPL Latest Status: Stage={latest['Stage_Daily']}, Score={latest['Score']}, Trend={latest.get('Trend_Template', 'N/A')}")
    
    # Detailed Rule Check for the latest row
    rules = {
        "Rule 1": (latest["Close"] > latest["SMA150"]) & (latest["Close"] > latest["SMA200"]),
        "Rule 2": latest["SMA150"] > latest["SMA200"],
        "Rule 3": latest["SMA200"] > latest["SMA200_prev20"],
        "Rule 4": (latest["SMA50"] > latest["SMA150"]) & (latest["SMA50"] > latest["SMA200"]),
        "Rule 5": latest["Close"] > latest["SMA50"],
        "Rule 6": latest["Close"] >= (latest["Lo_52W"] * 1.30),
        "Rule 7": latest["Close"] >= (latest["Hi_52W"] * 0.75),
    }
    print("Rule Failures:")
    for r, v in rules.items():
        if not v: print(f"- {r} FAILED")

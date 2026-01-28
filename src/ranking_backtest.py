"""
Advanced Ranking-based Backtester.
Maintains 10 Long and 10 Short slots by ranking a large universe of stocks daily.
"""

import logging
import pandas as pd
import numpy as np
import yfinance as yf
from backtest import BacktestEngine
from concurrent.futures import ThreadPoolExecutor
import matplotlib.pyplot as plt

# Expanded Universe (Nasdaq 100 + S&P 500 Representatives)
universe = list(dict.fromkeys([
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO", "COST", "NFLX",
    "ADBE", "AMD", "ASML", "AZN", "GILD", "INTC", "PEP", "PYPL", "SNOW", "DIS",
    "PFE", "MMM", "CVS", "SNAP", "BABA", "JNJ", "WMT", "V", "MA", "PG",
    "HD", "ABBV", "KO", "BAC", "VZ", "ADX", "CSCO", "XOM", "CVX", "CRM",
    "TMO", "ABT", "LIN", "ORCL", "ACN", "CMCSA", "DHR", "NEE", "TXN", "PM",
    "UNP", "INTU", "LOW", "UPS", "RTX", "BMY", "HON", "AMGN", "GE", "ISRG",
    "DE", "T", "CAT", "AXP", "GS", "MS", "SPGI", "BLK", "MDLZ", "TJX",
    "AMT", "PLD", "LMT", "LRCX", "ADI", "EL", "MU", "SCHW", "CI"
]))

class RankingEngine:
    def __init__(self, tickers, num_slots=10):
        self.tickers = tickers
        self.num_slots = num_slots
        self.data_cache = {}
        self.long_slots = [] # List of active (Ticker, EntryPrice, EntryDate)
        self.portfolio_history = []
        self.trade_log = [] # List of completed trades

    def load_data(self):
        print(f"Loading data for {len(self.tickers)} tickers...")
        def fetch(t):
            try:
                engine = BacktestEngine(t, period="10y")
                engine.prepare_data()
                return t, engine.data
            except:
                return t, None

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(fetch, self.tickers))
        
        for t, data in results:
            if data is not None:
                self.data_cache[t] = data
        print(f"Data loaded for {len(self.data_cache)} tickers.")

    def run(self, exit_strategy="trend_guardian"):
        if not self.data_cache:
            self.load_data()

        # Create a unified date index from all data
        all_dfs = []
        for t, df in self.data_cache.items():
            all_dfs.append(df[["Close"]].rename(columns={"Close": t}))
        
        market_index = pd.concat(all_dfs, axis=1).index.sort_values().unique()
        market_index = market_index[market_index >= pd.Timestamp("2018-01-01").tz_localize(market_index.tz)]
        
        print(f"Starting {exit_strategy} simulation from {market_index[0]}...")
        
        total_equity = 100.0
        self.long_slots = []
        self.portfolio_history = []
        
        for i in range(1, len(market_index)):
            date = market_index[i]
            prev_date = market_index[i-1]
            
            # 1. Calculate Daily Returns for Active Slots
            daily_sum_returns = 0.0
            
            # Use actual daily change
            for ticker, entry_p, _ in self.long_slots:
                df = self.data_cache[ticker]
                if date in df.index and prev_date in df.index:
                    ret = (df.loc[date, "Close"] - df.loc[prev_date, "Close"]) / df.loc[prev_date, "Close"]
                    daily_sum_returns += ret
            
            # Update Equity (Long-only: 10 slots)
            daily_pct = (daily_sum_returns / self.num_slots) * 100
            total_equity *= (1 + daily_pct / 100)

            # 2. Manage Portfolio (Exit and Fill)
            # Exit Longs
            for j in reversed(range(len(self.long_slots))):
                ticker, entry_price, entry_date = self.long_slots[j]
                df = self.data_cache[ticker]
                if date in df.index:
                    row = df.loc[date]
                    stage = row["Stage_Daily"]
                    score = row["Score"]
                    
                    exit_triggered = False
                    
                    if exit_strategy == "trend_guardian":
                        if stage != "Stage 2": exit_triggered = True
                    
                    elif exit_strategy == "sma_trailing":
                        # Exit if price < SMA20
                        if row["Close"] < row["SMA20"] or stage != "Stage 2": 
                             exit_triggered = True
                             
                    elif exit_strategy == "score_decay":
                        # Exit if Score > 8 AND cooling off (Score < 3 days ago)
                        prev_3 = df.loc[:date].index[-4] if len(df.loc[:date]) > 4 else None
                        if stage != "Stage 2":
                            exit_triggered = True
                        elif score > 8:
                            if prev_3 and score < df.loc[prev_3, "Score"]:
                                exit_triggered = True

                    if exit_triggered:
                        # Record completed trade
                        exit_price = row["Close"]
                        perf = (exit_price - entry_price) / entry_price
                        self.trade_log.append({
                            "Ticker": ticker,
                            "Entry_Date": entry_date,
                            "Exit_Date": date,
                            "Entry_Price": entry_price,
                            "Exit_Price": exit_price,
                            "Return (%)": perf * 100,
                            "Duration": (date - entry_date).days
                        })
                        self.long_slots.pop(j)
                else:
                    self.long_slots.pop(j)

            # Fill Slots
            if date in market_index:
                candidates_long = []
                active_tickers = [s[0] for s in self.long_slots]
                
                for t, df in self.data_cache.items():
                    if t in active_tickers or date not in df.index: continue
                    row = df.loc[date]
                    
                    # Long Entry: Stage 2 + Trend Template + Score < 4
                    if row["Stage_Daily"] == "Stage 2" and row.get("Trend_Template", False) and row["Score"] <= 4:
                        candidates_long.append((t, row["Score"], row["Close"]))

                # Sort longs by Score (lower is better/tighter)
                candidates_long.sort(key=lambda x: x[1])

                while len(self.long_slots) < self.num_slots and candidates_long:
                    t, _, p = candidates_long.pop(0)
                    self.long_slots.append((t, p, date))

            self.portfolio_history.append({
                "Date": date,
                "Equity": total_equity,
                "Long_Count": len(self.long_slots)
            })

        return pd.DataFrame(self.portfolio_history).set_index("Date"), pd.DataFrame(self.trade_log)

    def get_ticker_trades(self, ticker, exit_strategy="trend_guardian"):
        """Calculate theoretical trades for a single ticker ignoring portfolio constraints."""
        if ticker not in self.data_cache:
            return pd.DataFrame()
            
        df = self.data_cache[ticker]
        trades = []
        active_trade = None
        
        for date, row in df.iterrows():
            stage = row["Stage_Daily"]
            score = row["Score"]
            trend_pass = row.get("Trend_Template", False)
            
            if active_trade is None:
                # Entry Logic: Stage 2 + Trend + Score < 4
                if stage == "Stage 2" and trend_pass and score <= 4:
                    active_trade = {
                        "Ticker": ticker,
                        "Entry_Date": date,
                        "Entry_Price": row["Close"]
                    }
            else:
                # Exit Logic
                exit_triggered = False
                if exit_strategy == "trend_guardian":
                    if stage != "Stage 2": exit_triggered = True
                elif exit_strategy == "sma_trailing":
                    if row["Close"] < row["SMA20"] or stage != "Stage 2": exit_triggered = True
                elif exit_strategy == "score_decay":
                    prev_3_idx = df.index.get_loc(date) - 3
                    prev_3_score = df.iloc[prev_3_idx]["Score"] if prev_3_idx >= 0 else score
                    if stage != "Stage 2": exit_triggered = True
                    elif score > 8 and score < prev_3_score: exit_triggered = True
                
                if exit_triggered:
                    active_trade["Exit_Date"] = date
                    active_trade["Exit_Price"] = row["Close"]
                    perf = (active_trade["Exit_Price"] - active_trade["Entry_Price"]) / active_trade["Entry_Price"]
                    active_trade["Return (%)"] = perf * 100
                    active_trade["Duration"] = (date - active_trade["Entry_Date"]).days
                    trades.append(active_trade)
                    active_trade = None
                    
        return pd.DataFrame(trades)

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    engine = RankingEngine(universe)
    
def get_stats(res):
    daily_ret = res["Equity"].pct_change().fillna(0)
    ann_vol = daily_ret.std() * np.sqrt(252) * 100
    cum_ret = ((res["Equity"].iloc[-1] / res["Equity"].iloc[0]) - 1) * 100
    
    # CAGR calculation
    num_years = (res.index[-1] - res.index[0]).days / 365.25
    cagr = ((res["Equity"].iloc[-1] / res["Equity"].iloc[0])**(1/num_years) - 1) * 100
    
    # Max Drawdown
    rolling_max = res["Equity"].cummax()
    drawdown = (res["Equity"] - rolling_max) / rolling_max
    max_dd = drawdown.min() * 100
    
    return cum_ret, ann_vol, max_dd, cagr

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    engine = RankingEngine(universe)
    
    print("Running Long-Only Comparison vs SPY...")
    # Best Long strategy: Trend Guardian
    res_tg, _ = engine.run(exit_strategy="trend_guardian")
    
    # Download SPY
    start_date = res_tg.index[0]
    end_date = res_tg.index[-1]
    spy = yf.Ticker("SPY").history(start=start_date, end=end_date)
    spy.index = spy.index.tz_localize(None)
    spy_daily = spy["Close"].pct_change().fillna(0)
    spy_equity = (1 + spy_daily).cumprod() * 100
    res_spy = pd.DataFrame({"Equity": spy_equity}, index=spy.index)
    
    stats_tg = get_stats(res_tg)
    stats_spy = get_stats(res_spy)
    
    # Sharpe Ratio: (CAGR / Vol)
    sharpe_tg = stats_tg[3] / stats_tg[1] if stats_tg[1] > 0 else 0
    sharpe_spy = stats_spy[3] / stats_spy[1] if stats_spy[1] > 0 else 0

    print("\n" + "="*80)
    print(f"{'METRIC':<20} | {'TREND GUARDIAN':<25} | {'SPY (BENCHMARK)':<25}")
    print("-" * 80)
    print(f"{'Total Return':<20} | {stats_tg[0]:>22.2f}% | {stats_spy[0]:>22.2f}%")
    print(f"{'CAGR':<20} | {stats_tg[3]:>22.2f}% | {stats_spy[3]:>22.2f}%")
    print(f"{'Ann. Volatility':<20} | {stats_tg[1]:>22.2f}% | {stats_spy[1]:>22.2f}%")
    print(f"{'Max Drawdown':<20} | {stats_tg[2]:>22.2f}% | {stats_spy[2]:>22.2f}%")
    print(f"{'Sharpe Ratio':<20} | {sharpe_tg:>22.2f} | {sharpe_spy:>22.2f}")
    print("="*80)

    plt.figure(figsize=(14, 7))
    plt.plot(res_tg["Equity"], label=f"Trend Guardian (+{stats_tg[0]:.0f}%)", color="blue", linewidth=3)
    plt.plot(res_spy["Equity"], label=f"SPY Benchmark (+{stats_spy[0]:.0f}%)", color="gray", linestyle="--", alpha=0.8)
    
    plt.title("Trend Guardian (Long-Only) vs SPY Benchmark (2018-2026)")
    plt.ylabel("Equity Value (Start = 100)")
    plt.legend()
    plt.grid(True, which='both', linestyle='--', alpha=0.5)
    plt.savefig("long_only_vs_spy.png")
    
    print("\nComparison complete. Plot saved to long_only_vs_spy.png")

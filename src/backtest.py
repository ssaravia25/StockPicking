"""
Strategy Backtester for Trading Matrix.

Implements vectorized backtesting for:
- Long on Stage 2 and Score < 4.
- Short on Stage 3 and Score > 7.
- Exit on status change.
"""

import logging
import pandas as pd
import numpy as np
import yfinance as yf
from typing import Optional, Dict, Any
try:
    from .screener import classify_stage_vectorized, calculate_score_vectorized, calculate_short_score_vectorized
except ImportError:
    from screener import classify_stage_vectorized, calculate_score_vectorized, calculate_short_score_vectorized

logger = logging.getLogger(__name__)

class BacktestEngine:
    def __init__(self, symbol: str, period: str = "5y"):
        self.symbol = symbol
        self.period = period
        self.data: Optional[pd.DataFrame] = None
        self.results: Optional[Dict[str, Any]] = None

    def prepare_data(self):
        """Download data and calculate all required indicators."""
        logger.info(f"Downloading data for {self.symbol}...")
        ticker = yf.Ticker(self.symbol)
        df = ticker.history(period=self.period)
        
        if len(df) < 250:
            raise ValueError(f"Insufficient data for {self.symbol}")

        # Basic SMAs
        df["SMA10"] = df["Close"].rolling(10).mean()
        df["SMA20"] = df["Close"].rolling(20).mean()
        df["SMA50"] = df["Close"].rolling(50).mean()
        df["SMA150"] = df["Close"].rolling(150).mean()
        df["SMA200"] = df["Close"].rolling(200).mean()
        df["SMA200_prev20"] = df["SMA200"].shift(20)

        # ATR for risk management
        high_low = df["High"] - df["Low"]
        high_close = np.abs(df["High"] - df["Close"].shift())
        low_close = np.abs(df["Low"] - df["Close"].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df["ATR"] = true_range.rolling(14).mean()

        # Vectorized Stage and Score
        df["Stage_Daily"] = classify_stage_vectorized(df)
        df["Score"] = calculate_score_vectorized(df)
        df["Short_Score"] = calculate_short_score_vectorized(df)

        # Vectorized Trend Template (8 Rules)
        df["Hi_52W"] = df["High"].rolling(252).max()
        df["Lo_52W"] = df["Low"].rolling(252).min()
        
        df["Rule1"] = (df["Close"] > df["SMA150"]) & (df["Close"] > df["SMA200"])
        df["Rule2"] = df["SMA150"] > df["SMA200"]
        df["Rule3"] = df["SMA200"] > df["SMA200_prev20"]
        df["Rule4"] = (df["SMA50"] > df["SMA150"]) & (df["SMA50"] > df["SMA200"])
        df["Rule5"] = df["Close"] > df["SMA50"]
        df["Rule6"] = df["Close"] >= (df["Lo_52W"] * 1.30)
        df["Rule7"] = df["Close"] >= (df["Hi_52W"] * 0.75)
        
        df["Trend_Template"] = df["Rule1"] & df["Rule2"] & df["Rule3"] & df["Rule4"] & df["Rule5"] & df["Rule6"] & df["Rule7"]

        self.data = df.dropna(subset=["SMA200", "SMA200_prev20", "Score", "Hi_52W"])
        return self

    def run_strategy(self, strategy_type: str = "fresh_breakout", use_trend_template: bool = True):
        """
        Simulate trades based on specific strategy rules.
        
        Types:
        - 'original': Stage 2 + Score < 4
        - 'fresh_breakout': Stage 1/4 -> Stage 2 + Score < 4
        - 'swing_pullback': In Stage 2, Score 7 -> 4
        - 'distribution_trap' (Short): Stage 2 -> Stage 3 + Score > 7
        - 'bear_flag' (Short): In Stage 4, Score <= 7 (Price rallied to MAs)
        - 'stage3_breakdown' (Short): Stage 3 -> Stage 4 transition
        """
        if self.data is None:
            self.prepare_data()

        df = self.data.copy()
        df["Prev_Stage"] = df["Stage_Daily"].shift(1)
        df["Prev_Score"] = df["Score"].shift(1)
        df["Prev_Short_Score"] = df["Short_Score"].shift(1)

        # Position status: 1 for long, -1 for short, 0 for cash
        position = 0
        trades = []
        entry_price = 0
        entry_date = None
        
        for i in range(1, len(df)):
            current_row = df.iloc[i]
            prev_row = df.iloc[i-1]
            date = df.index[i]
            close = current_row["Close"]
            
            stage = current_row["Stage_Daily"]
            prev_stage = prev_row["Stage_Daily"]
            score = current_row["Score"]
            prev_score = prev_row["Score"]
            trend_ok = current_row["Trend_Template"] if use_trend_template else True

            # Entry Logic
            if position == 0:
                if strategy_type == "original":
                    if stage == "Stage 2" and score < 4 and trend_ok:
                        position = 1
                
                elif strategy_type == "fresh_breakout":
                    # Transition from 1 or 4 to 2
                    if prev_stage in ["Stage 1 / Neutral", "Stage 4"] and stage == "Stage 2" and score < 4 and trend_ok:
                        position = 1
                
                elif strategy_type == "swing_pullback":
                    # Already in Stage 2, was extended (Score 7 or 10), now pulling back to 4
                    if stage == "Stage 2" and prev_score >= 7 and score <= 4 and trend_ok:
                        position = 1
                
                elif strategy_type == "distribution_trap":
                    # Transition from 2 to 3 with High Score (Short)
                    if prev_stage == "Stage 2" and stage == "Stage 3" and current_row["Short_Score"] <= 7:
                        position = -1
                
                elif strategy_type == "bear_flag":
                    # Already in Stage 4, price rallied to touch or get near MAs (Short Score 1-7)
                    if stage == "Stage 4" and current_row["Short_Score"] <= 7:
                        position = -1
                
                elif strategy_type == "stage3_breakdown":
                    # Transition from 3 to 4
                    if prev_stage == "Stage 3" and stage == "Stage 4":
                        position = -1

                if position != 0:
                    entry_price = close
                    entry_date = date

            # Exit Logic
            elif position == 1:
                # Common exit for all longs: Lose Stage 2 OR Trend Template Fail
                exit_long = False
                if stage != "Stage 2": exit_long = True
                if use_trend_template and not trend_ok: exit_long = True
                
                # Strategy specific exits
                if strategy_type == "original" or strategy_type == "fresh_breakout":
                    if score >= 10: exit_long = True # Overextended
                
                if exit_long:
                    trades.append({
                        "Type": "Long",
                        "Entry_Date": entry_date,
                        "Exit_Date": date,
                        "Entry_Price": entry_price,
                        "Exit_Price": close,
                        "Return_Pct": (close - entry_price) / entry_price * 100
                    })
                    position = 0

            elif position == -1:
                # Common exit for all shorts: Lose Stage 3/4 OR Stage 2 reversal
                exit_short = False
                if stage in ["Stage 2", "Stage 1 / Neutral"]: exit_short = True
                
                # Bear flag specific: exit if price moves too far above MAs
                if strategy_type == "bear_flag" and score > 7: exit_short = True
                
                if exit_short:
                    trades.append({
                        "Type": "Short",
                        "Entry_Date": entry_date,
                        "Exit_Date": date,
                        "Entry_Price": entry_price,
                        "Exit_Price": close,
                        "Return_Pct": (entry_price - close) / entry_price * 100
                    })
                    position = 0

        # Final Close if still in position
        if position != 0:
            last_row = df.iloc[-1]
            last_close = last_row["Close"]
            trades.append({
                "Type": "Long" if position == 1 else "Short",
                "Entry_Date": entry_date,
                "Exit_Date": df.index[-1],
                "Entry_Price": entry_price,
                "Exit_Price": last_close,
                "Return_Pct": ((last_close - entry_price) / entry_price * 100) if position == 1 else ((entry_price - last_close) / entry_price * 100)
            })

        self.calculate_metrics(trades, df)
        return self

    def calculate_metrics(self, trades, df):
        """Calculate summary statistics."""
        if not trades:
            self.results = {
                "Ticker": self.symbol,
                "Total_Trades": 0,
                "Win_Rate_Pct": 0,
                "Avg_Trade_Pct": 0,
                "Strategy_Return_Pct": 0,
                "Buy_Hold_Return_Pct": round((df["Close"].iloc[-1] - df["Close"].iloc[0]) / df["Close"].iloc[0] * 100, 2)
            }
            return

        trade_df = pd.DataFrame(trades)
        
        win_rate = (trade_df["Return_Pct"] > 0).mean() * 100
        avg_trade = trade_df["Return_Pct"].mean()
        
        compounded = 1.0
        for r in trade_df["Return_Pct"]:
            compounded *= (1 + r/100)
        
        total_compounded_pct = (compounded - 1) * 100
        bh_return = (df["Close"].iloc[-1] - df["Close"].iloc[0]) / df["Close"].iloc[0] * 100

        self.results = {
            "Ticker": self.symbol,
            "Total_Trades": len(trade_df),
            "Win_Rate_Pct": round(win_rate, 2),
            "Avg_Trade_Pct": round(avg_trade, 2),
            "Strategy_Return_Pct": round(total_compounded_pct, 2),
            "Buy_Hold_Return_Pct": round(bh_return, 2),
            "Trade_Log": trade_df
        }

    def get_summary(self, strategy_name: str = "") -> str:
        if not self.results:
            return f"No results for {strategy_name} ({self.symbol})."
        
        res = self.results
        summary = (
            f"--- {strategy_name} Summary: {res['Ticker']} ---\n"
            f"Total Trades: {res['Total_Trades']}\n"
            f"Win Rate: {res.get('Win_Rate_Pct', 0)}%\n"
            f"Avg Trade: {res.get('Avg_Trade_Pct', 0)}%\n"
            f"Strategy Return: {res.get('Strategy_Return_Pct', 0)}%\n"
            f"Buy & Hold Return: {res.get('Buy_Hold_Return_Pct', 0)}%\n"
        )
        return summary

def run_backtest_variants(symbol: str):
    engine = BacktestEngine(symbol)
    engine.prepare_data()
    
    variants = ["original", "fresh_breakout", "swing_pullback", "distribution_trap", "bear_flag", "stage3_breakdown"]
    for v in variants:
        engine.run_strategy(strategy_type=v)
        print(engine.get_summary(strategy_name=v.upper()))

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    for ticker in ["INTC", "PYPL", "SNOW", "DIS", "BABA"]:
        print(f"\n{'='*20} {ticker} {'='*20}")
        run_backtest_variants(ticker)

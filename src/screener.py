"""
Optimized Stock Screener - Minervini & Stage Analysis

This module implements Mark Minervini's trading methodology with Stage Analysis
using vectorized operations for performance.
"""

import logging
from io import StringIO
from typing import List, Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# Cache for benchmark data to avoid multiple hits
BENCHMARK_DATA: Optional[pd.DataFrame] = None


def get_sp500_tickers() -> List[str]:
    """Fetch S&P 500 ticker symbols from Wikipedia."""
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        # Use StringIO to avoid FutureWarning
        tables = pd.read_html(StringIO(response.text))
        tickers = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        logger.info(f"Fetched {len(tickers)} S&P 500 tickers")
        return tickers
    except Exception as e:
        logger.error(f"Failed to fetch S&P 500 tickers: {e}")
        return []


def classify_stage_vectorized(df: pd.DataFrame) -> pd.Series:
    """
    Classify market stage using vectorized operations.

    Stages:
    - Stage 2 (Uptrend): Price > SMA200, SMA200 rising, SMA50 > SMA150
    - Stage 4 (Downtrend): Price < SMA200, SMA200 falling, SMA50 < SMA150
    - Stage 3 (Distribution): Price < SMA150, SMA200 flat/rising
    - Stage 1 (Accumulation): Everything else
    """
    conditions = [
        # Stage 2: Uptrend
        (df["Close"] > df["SMA200"])
        & (df["SMA200"] > df["SMA200_prev20"])
        & (df["SMA50"] > df["SMA150"]),
        # Stage 4: Downtrend
        (df["Close"] < df["SMA200"])
        & (df["SMA200"] < df["SMA200_prev20"])
        & (df["SMA50"] < df["SMA150"]),
        # Stage 3: Distribution
        (df["Close"] < df["SMA150"]) & (df["SMA200"] >= df["SMA200_prev20"]),
    ]
    choices = ["Stage 2", "Stage 4", "Stage 3"]

    return pd.Series(
        np.select(conditions, choices, default="Stage 1 / Neutral"),
        index=df.index,
    )


def calculate_rs_score(df: pd.DataFrame, benchmark_df: pd.DataFrame) -> float:
    """
    Calculate a Relative Strength score comparing the stock against a benchmark.
    Uses weighted performance: (40% last 3m, 20% last 6m, 20% last 9m, 20% last 12m).
    """
    try:
        def get_perf(data: pd.DataFrame, days: int) -> float:
            if len(data) < days:
                return 0
            curr = data["Close"].iloc[-1]
            prev = data["Close"].iloc[-days]
            return (curr - prev) / prev

        # Calculate weighted performance for stock
        s_3m = get_perf(df, 63)  # 21 days * 3
        s_6m = get_perf(df, 126)
        s_9m = get_perf(df, 189)
        s_12m = get_perf(df, 252)
        s_score = (s_3m * 0.4) + (s_6m * 0.2) + (s_9m * 0.2) + (s_12m * 0.2)

        # Calculate weighted performance for benchmark
        b_3m = get_perf(benchmark_df, 63)
        b_6m = get_perf(benchmark_df, 126)
        b_9m = get_perf(benchmark_df, 189)
        b_12m = get_perf(benchmark_df, 252)
        b_score = (b_3m * 0.4) + (b_6m * 0.2) + (b_9m * 0.2) + (b_12m * 0.2)

        # Relative score (Stock score - Benchmark score)
        # This is a raw score, normalized rating 0-100 requires full market context.
        return round((s_score - b_score) * 100, 2)
    except Exception:
        return 0.0


def check_trend_template(df: pd.DataFrame) -> dict:
    """
    Check Mark Minervini's 8-Point Trend Template.
    Returns a dict with 'Pass' boolean and failed rules.
    """
    latest = df.iloc[-1]
    cp = latest["Close"]
    s50 = latest["SMA50"]
    s150 = latest["SMA150"]
    s200 = latest["SMA200"]
    s200_20 = latest["SMA200_prev20"]
    
    hi_52w = df["High"].rolling(252).max().iloc[-1]
    lo_52w = df["Low"].rolling(252).min().iloc[-1]

    rules = {
        "Rule 1: Price > 150 & 200 SMA": cp > s150 and cp > s200,
        "Rule 2: 150 SMA > 200 SMA": s150 > s200,
        "Rule 3: 200 SMA Trending Up": s200 > s200_20,
        "Rule 4: 50 SMA > 150 & 200": s50 > s150 and s50 > s200,
        "Rule 5: Price > 50 SMA": cp > s50,
        "Rule 6: Price 30% above Low": cp >= (lo_52w * 1.30),
        "Rule 7: Price within 25% of High": cp >= (hi_52w * 0.75),
    }
    
    passed_all = all(rules.values())
    failed = [k for k, v in rules.items() if not v]
    
    return {"status": "PASS" if passed_all else "FAIL", "failed_rules": failed}


def calculate_score_vectorized(df: pd.DataFrame) -> pd.Series:
    """
    Calculate risk score using vectorized operations.

    Scores:
    - 1-2: Buy zone (price within 3% of SMA10)
    - 4: Hold zone (price within 7% of SMA20)
    - 7: Extended (price within 15% of SMA50)
    - 10: Overextended
    - 11: Danger/Not in Stage 2
    """
    # Default score is 11 (danger)
    score = pd.Series(11, index=df.index)

    # Only score if in Stage 2 and price > SMA20
    in_stage2 = df["Stage_Daily"] == "Stage 2"
    above_sma20 = df["Close"] > df["SMA20"]
    valid = in_stage2 & above_sma20

    # Score 1: Within 3% of SMA10
    score = score.where(
        ~(valid & (df["Close"] <= df["SMA10"] * 1.03)), 1
    )

    # Score 4: Within 7% of SMA20 (but not score 1)
    score = score.where(
        ~(valid & (score == 11) & (df["Close"] <= df["SMA20"] * 1.07)), 4
    )

    # Score 7: Within 15% of SMA50 (but not score 1 or 4)
    score = score.where(
        ~(valid & (score == 11) & (df["Close"] <= df["SMA50"] * 1.15)), 7
    )

    # Score 10: Extended beyond 15% of SMA50
    score = score.where(
        ~(valid & (score == 11)), 10
    )

    return score


def calculate_short_score_vectorized(df: pd.DataFrame) -> pd.Series:
    """
    Calculate risk score for shorts using vectorized operations.
    Ranks 1-11 based on proximity to MAs from BELOW.
    """
    # Default score is 11 (danger)
    score = pd.Series(11, index=df.index)

    # Only score if in Stage 3 or 4 and price < SMA20
    in_short_stage = df["Stage_Daily"].isin(["Stage 3", "Stage 4"])
    below_sma20 = df["Close"] < df["SMA20"]
    valid = in_short_stage & below_sma20

    # Score 1: Within 3% of SMA10 (pullback/bear flag)
    # Price is between SMA10*0.97 and SMA10
    score = score.where(
        ~(valid & (df["Close"] >= df["SMA10"] * 0.97)), 1
    )

    # Score 4: Within 7% of SMA20
    score = score.where(
        ~(valid & (score == 11) & (df["Close"] >= df["SMA20"] * 0.93)), 4
    )

    # Score 7: Within 15% of SMA50
    score = score.where(
        ~(valid & (score == 11) & (df["Close"] >= df["SMA50"] * 0.85)), 7
    )

    return score


def analyze_ticker(symbol: str, benchmark_df: Optional[pd.DataFrame] = None) -> Optional[dict]:
    """
    Analyze a single ticker and return results dictionary.
    """
    try:
        ticker = yf.Ticker(symbol)
        history = ticker.history(period="2y")

        if len(history) < 250:
            logger.debug(f"{symbol}: Insufficient data ({len(history)} days)")
            return None

        # Calculate SMAs
        history["SMA10"] = history["Close"].rolling(10).mean()
        history["SMA20"] = history["Close"].rolling(20).mean()
        history["SMA50"] = history["Close"].rolling(50).mean()
        history["SMA150"] = history["Close"].rolling(150).mean()
        history["SMA200"] = history["Close"].rolling(200).mean()
        history["SMA200_prev20"] = history["SMA200"].shift(20)
        
        # Volume analysis
        history["Vol_Avg_50"] = history["Volume"].rolling(50).mean()

        # Classify stage (vectorized)
        history["Stage_Daily"] = classify_stage_vectorized(history)

        # Drop NaN rows
        history = history.dropna(
            subset=["SMA10", "SMA20", "SMA50", "SMA150", "SMA200", "SMA200_prev20"]
        )

        if history.empty:
            return None

        # Calculate score (vectorized)
        history["Score"] = calculate_score_vectorized(history)
        history["Short_Score"] = calculate_short_score_vectorized(history)

        # Get latest values
        latest = history.iloc[-1]
        close_price = latest["Close"]
        sma10 = latest["SMA10"]
        sma20 = latest["SMA20"]
        sma50 = latest["SMA50"]
        sma200 = latest["SMA200"]
        vol_ratio = round(latest["Volume"] / latest["Vol_Avg_50"], 2) if latest["Vol_Avg_50"] > 0 else 0

        # Calculate ATR for risk management
        high_low = history["High"] - history["Low"]
        high_close = abs(history["High"] - history["Close"].shift(1))
        low_close = abs(history["Low"] - history["Close"].shift(1))
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = true_range.rolling(14).mean().iloc[-1]

        # Current stage and score
        stage = latest["Stage_Daily"]
        score = int(latest["Score"])

        # Risk calculations
        risk_pct = round(((2 * atr) / close_price) * 100, 2)
        stop_loss = round(close_price - (2 * atr), 2)

        # Find date since current stage began
        current_stage = stage
        date_since_stage = history.index[-1].strftime("%Y-%m-%d")
        for i in range(len(history) - 2, -1, -1):
            if history["Stage_Daily"].iloc[i] != current_stage:
                date_since_stage = history.index[i + 1].strftime("%Y-%m-%d")
                break

        # Stage 30 days ago
        stage_30_days_ago = "N/A"
        if len(history) > 30:
            stage_30_days_ago = history["Stage_Daily"].iloc[-31]

        # Get sector info
        info = ticker.info
        sector = info.get("sector", "N/A")

        return {
            "Ticker": symbol,
            "Sector": sector,
            "Price": round(close_price, 2),
            "Score": score,
            "Stage": stage,
            "Date_Since_Current_Stage": date_since_stage,
            "Stage_30_Days_Ago": stage_30_days_ago,
            "Abv10": "Y" if close_price > sma10 else "N",
            "Abv20": "Y" if close_price > sma20 else "N",
            "Abv50": "Y" if close_price > sma50 else "N",
            "Abv200": "Y" if close_price > sma200 else "N",
            "Stop_Loss": stop_loss,
            "Risk_%": risk_pct,
            "ATR": round(atr, 2),
            "RS_Score": calculate_rs_score(history, benchmark_df) if benchmark_df is not None else 0,
            "Trend_Template": check_trend_template(history)["status"],
            "Short_Score": int(latest["Short_Score"]),
            "Vol_Ratio": vol_ratio
        }

    except Exception as e:
        logger.warning(f"Error analyzing {symbol}: {e}")
        return None


def run_screener(tickers: List[str]) -> pd.DataFrame:
    """
    Run the trading matrix screener on a list of tickers.
    """
    logger.info(f"Analyzing {len(tickers)} tickers...")
    
    # Fetch SPY as benchmark
    logger.info("Fetching SPY benchmark data...")
    benchmark_df = yf.Ticker("SPY").history(period="2y")
    
    results = []

    for i, symbol in enumerate(tickers):
        if (i + 1) % 50 == 0:
            logger.info(f"Progress: {i + 1}/{len(tickers)}")

        result = analyze_ticker(symbol, benchmark_df)
        if result:
            results.append(result)

    df = pd.DataFrame(results)
    logger.info(f"Completed analysis. {len(df)} tickers processed successfully.")
    return df


def filter_buy_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter for best buy candidates: Stage 2 with Score <= 4.

    Args:
        df: Full screening results DataFrame

    Returns:
        Filtered DataFrame with buy candidates only
    """
    if df.empty:
        return df

    mask = (df["Stage"] == "Stage 2") & (df["Score"] <= 4) & (df["Trend_Template"] == "PASS")
    filtered = df[mask].copy()
    filtered = filtered.sort_values("Score")

    logger.info(f"Found {len(filtered)} buy candidates out of {len(df)} stocks")
    return filtered

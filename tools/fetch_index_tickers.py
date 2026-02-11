"""
Fetch index constituents (tickers) for major indices.
Sources:
  - S&P 500: Wikipedia
  - Nasdaq 100: Wikipedia
  - Eurostoxx 50: Wikipedia
  - Russell 2000: iShares IWM ETF holdings

Output: CSV files saved to .tmp/ with ticker lists.
"""

import requests
import pandas as pd
import io
import os
import sys

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tmp")


def fetch_sp500():
    """Fetch S&P 500 constituents from Wikipedia."""
    print("Fetching S&P 500...")
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)
    result = df[["Symbol", "Security", "GICS Sector", "GICS Sub-Industry"]].copy()
    result.columns = ["Ticker", "Name", "Sector", "SubIndustry"]
    print(f"  S&P 500: {len(result)} tickers")
    return result


def fetch_nasdaq100():
    """Fetch Nasdaq 100 constituents from Wikipedia."""
    print("Fetching Nasdaq 100...")
    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    # Find the right table by looking for 'Ticker' column with ~100 rows
    df = None
    for t in tables:
        if "Ticker" in t.columns and 80 < len(t) < 120:
            df = t
            break
    if df is None:
        raise ValueError("Could not find Nasdaq 100 constituents table on Wikipedia")
    result = df[["Ticker", "Company"]].copy()
    result.columns = ["Ticker", "Name"]
    print(f"  Nasdaq 100: {len(result)} tickers")
    return result


def fetch_eurostoxx50():
    """Fetch Eurostoxx 50 constituents from Wikipedia."""
    print("Fetching Eurostoxx 50...")
    url = "https://en.wikipedia.org/wiki/EURO_STOXX_50"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    # Find the right table
    df = None
    for t in tables:
        if "Ticker" in t.columns and 40 <= len(t) <= 55:
            df = t
            break
    if df is None:
        raise ValueError("Could not find Eurostoxx 50 constituents table on Wikipedia")
    result = df[["Ticker", "Name"]].copy()
    print(f"  Eurostoxx 50: {len(result)} tickers")
    return result


def fetch_russell2000():
    """Fetch Russell 2000 constituents from iShares IWM ETF holdings."""
    print("Fetching Russell 2000 (via iShares IWM)...")
    url = (
        "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/"
        "1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"
    )
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    lines = resp.text.split("\n")
    # Find the header row
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Ticker,") or line.startswith('"Ticker"'):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find header row in IWM CSV")
    csv_text = "\n".join(lines[header_idx:])
    df = pd.read_csv(io.StringIO(csv_text))
    # Filter to equities only, exclude placeholder tickers
    df = df[(df["Asset Class"] == "Equity") & (df["Ticker"] != "-") & (df["Ticker"].notna())]
    result = df[["Ticker", "Name"]].copy()
    result = result.drop_duplicates(subset="Ticker").reset_index(drop=True)
    print(f"  Russell 2000: {len(result)} tickers")
    return result


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    indices = {
        "sp500": fetch_sp500,
        "nasdaq100": fetch_nasdaq100,
        "eurostoxx50": fetch_eurostoxx50,
        "russell2000": fetch_russell2000,
    }

    all_results = {}
    for name, fetcher in indices.items():
        try:
            df = fetcher()
            path = os.path.join(OUTPUT_DIR, f"{name}_tickers.csv")
            df.to_csv(path, index=False)
            print(f"  Saved to {path}")
            all_results[name] = df
        except Exception as e:
            print(f"  ERROR fetching {name}: {e}", file=sys.stderr)

    # Summary
    print("\n=== Summary ===")
    total = 0
    for name, df in all_results.items():
        print(f"  {name}: {len(df)} tickers")
        total += len(df)
    print(f"  TOTAL: {total} tickers across {len(all_results)} indices")

    # Combined file with index column
    if all_results:
        frames = []
        for name, df in all_results.items():
            subset = df[["Ticker", "Name"]].copy()
            subset["Index"] = name
            frames.append(subset)
        combined = pd.concat(frames, ignore_index=True)
        combined_path = os.path.join(OUTPUT_DIR, "all_index_tickers.csv")
        combined.to_csv(combined_path, index=False)
        print(f"\n  Combined file: {combined_path}")


if __name__ == "__main__":
    main()

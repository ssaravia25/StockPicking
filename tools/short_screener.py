"""
Short Only Screener - Nasdaq 100

Scans the Nasdaq 100 for short candidates using Stage Analysis.
Filters for stocks in distribution (Stage 3) or downtrend (Stage 4)
with weak trend templates and high risk scores.

Usage:
    python tools/short_screener.py                  # Full scan + export to Sheets
    python tools/short_screener.py --no-sheets      # Console only, skip Sheets export
    python tools/short_screener.py --stage 3        # Only Stage 3
    python tools/short_screener.py --stage 4        # Only Stage 4
"""

import argparse
import base64
import json
import logging
import os
import sys

import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.fetch_index_tickers import fetch_nasdaq100
from src.screener import run_screener
from src.sheets_exporter import export_to_sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def filter_short_candidates(df: pd.DataFrame, stage_filter: str = None) -> pd.DataFrame:
    """
    Filter for short candidates from full screening results.

    Criteria:
    - Stage 3 (Distribution) or Stage 4 (Downtrend)
    - Trend Template = FAIL (not in a healthy uptrend)
    - Short_Score <= 7 (price near MAs from below = good short entry)

    Sort: Short_Score ASC (best entry first), then RS_Score ASC (weakest first)
    """
    if df.empty:
        return df

    # Stage filter
    if stage_filter == "3":
        stage_mask = df["Stage"] == "Stage 3"
    elif stage_filter == "4":
        stage_mask = df["Stage"] == "Stage 4"
    else:
        stage_mask = df["Stage"].isin(["Stage 3", "Stage 4"])

    # Core filter: bad stage + trend template failing
    mask = stage_mask & (df["Trend_Template"] == "FAIL")

    filtered = df[mask].copy()

    # Sort: best short entries first (low Short_Score), then weakest RS
    filtered = filtered.sort_values(
        ["Short_Score", "RS_Score"], ascending=[True, True]
    )

    logger.info(f"Found {len(filtered)} short candidates out of {len(df)} stocks")
    return filtered


def print_short_summary(df: pd.DataFrame, all_df: pd.DataFrame):
    """Print a formatted summary of short candidates."""
    print("\n" + "=" * 80)
    print("SHORT ONLY SCREENER - NASDAQ 100")
    print("=" * 80)

    # Stage distribution across all scanned stocks
    stage_counts = all_df["Stage"].value_counts()
    print("\n--- Universe Stage Distribution ---")
    for stage, count in stage_counts.items():
        pct = count / len(all_df) * 100
        print(f"  {stage}: {count} ({pct:.0f}%)")

    if df.empty:
        print("\nNo short candidates found matching criteria.")
        return

    print(f"\n--- {len(df)} Short Candidates ---\n")

    # Display columns for shorts
    display_cols = [
        "Ticker", "Price", "Stage", "Short_Score", "Score",
        "Trend_Template", "RS_Score", "Abv10", "Abv20", "Abv50", "Abv200",
        "Risk_%", "ATR", "Vol_Ratio", "Date_Since_Current_Stage", "Sector",
    ]
    existing_cols = [c for c in display_cols if c in df.columns]

    # Print table
    print(df[existing_cols].to_string(index=False))

    # Summary stats
    print(f"\n--- Summary ---")
    print(f"  Total candidates: {len(df)}")
    stage3 = len(df[df["Stage"] == "Stage 3"])
    stage4 = len(df[df["Stage"] == "Stage 4"])
    print(f"  Stage 3 (Distribution): {stage3}")
    print(f"  Stage 4 (Downtrend): {stage4}")
    print(f"  Avg RS Score: {df['RS_Score'].mean():.2f}")
    print(f"  Weakest RS: {df.iloc[df['RS_Score'].argmin()]['Ticker']} ({df['RS_Score'].min():.2f})")

    # Top 5 by weakness
    print(f"\n--- Top 5 Weakest (Lowest RS) ---")
    top5 = df.nsmallest(5, "RS_Score")
    for _, row in top5.iterrows():
        print(f"  {row['Ticker']:6s}  Stage={row['Stage']}  ShortScore={row['Short_Score']}  RS={row['RS_Score']:.2f}  Price=${row['Price']:.2f}")

    print("=" * 80)


def export_shorts_to_sheets(df: pd.DataFrame, all_df: pd.DataFrame):
    """Export short candidates to Google Sheets."""
    creds_b64 = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
    sheet_name = os.getenv("SPREADSHEET_NAME", "Trading_Matrix_PremiumSS_Momentum26")

    if not creds_b64:
        logger.warning("GOOGLE_SHEETS_CREDENTIALS not set, skipping Sheets export")
        return None

    credentials_dict = json.loads(base64.b64decode(creds_b64).decode("utf-8"))

    # Export short candidates
    url = export_to_sheets(
        df=df,
        credentials_dict=credentials_dict,
        spreadsheet_name=sheet_name,
        worksheet_name="Short_Candidates",
    )

    # Also export full Nasdaq 100 scan for reference
    export_to_sheets(
        df=all_df,
        credentials_dict=credentials_dict,
        spreadsheet_name=sheet_name,
        worksheet_name="NDX100_Full_Scan",
    )

    if url:
        logger.info(f"Exported to Google Sheets: {url}")
    return url


def main():
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Short Only Screener - Nasdaq 100")
    parser.add_argument("--no-sheets", action="store_true", help="Skip Google Sheets export")
    parser.add_argument("--stage", choices=["3", "4"], default=None, help="Filter specific stage (3 or 4)")
    args = parser.parse_args()

    # Step 1: Fetch Nasdaq 100 tickers
    logger.info("Fetching Nasdaq 100 tickers...")
    ndx_df = fetch_nasdaq100()
    tickers = ndx_df["Ticker"].tolist()
    logger.info(f"Got {len(tickers)} Nasdaq 100 tickers")

    # Step 2: Run full screener
    logger.info("Running screener on Nasdaq 100...")
    all_results = run_screener(tickers)

    if all_results.empty:
        logger.error("Screener returned no results")
        return

    logger.info(f"Screened {len(all_results)} tickers successfully")

    # Step 3: Filter short candidates
    shorts = filter_short_candidates(all_results, stage_filter=args.stage)

    # Step 4: Print summary
    print_short_summary(shorts, all_results)

    # Step 5: Export to Sheets
    if not args.no_sheets:
        url = export_shorts_to_sheets(shorts, all_results)
        if url:
            print(f"\nGoogle Sheets: {url}")

    # Step 6: Save CSV locally
    os.makedirs(".tmp", exist_ok=True)
    shorts.to_csv(".tmp/short_candidates.csv", index=False)
    all_results.to_csv(".tmp/ndx100_full_scan.csv", index=False)
    logger.info("Saved CSVs to .tmp/")


if __name__ == "__main__":
    main()

"""
ETF Screener — Stage Analysis with grid chart + daily email.

Analyzes a list of popular ETFs using Minervini methodology,
generates a grid chart, and sends results via email.

Usage:
    python3 tools/etf_screener.py              # Full run (analyze + email)
    python3 tools/etf_screener.py --no-email   # Analyze only, skip email
"""
import os
import sys
import logging
import smtplib
import argparse
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

import anthropic
import numpy as np
import pandas as pd
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from src.screener import (
    classify_stage_vectorized,
    calculate_score_vectorized,
    calculate_short_score_vectorized,
    calculate_rs_score,
    check_trend_template,
)

# Load .env if available
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
RECIPIENTS = ["sergiosar@gmail.com", "sgseaux@gmail.com"]

ETF_LIST = [
    # Major indices
    "SPY",   # S&P 500
    "QQQ",   # Nasdaq 100
    "IWM",   # Russell 2000
    "DIA",   # Dow Jones
    # Sectors
    "XLK",   # Technology
    "XLF",   # Financials
    "XLE",   # Energy
    "XLV",   # Healthcare
    "XLI",   # Industrials
    "XLC",   # Communication Services
    "XLY",   # Consumer Discretionary
    "XLP",   # Consumer Staples
    "XLU",   # Utilities
    "XLB",   # Materials
    "XLRE",  # Real Estate
    # Thematic / popular
    "SMH",   # Semiconductors
    "GLD",   # Gold
    "TLT",   # Long-term Treasuries
    "HYG",   # High Yield Bonds
    "EEM",   # Emerging Markets
    "IBIT",  # Bitcoin
    "BTAL",  # Anti-Beta (market neutral)
    "FXE",   # Euro Currency
]

STAGE_COLORS = {
    "Stage 2": "#c8e6c9",
    "Stage 3": "#fff9c4",
    "Stage 4": "#ffcdd2",
    "Stage 1 / Neutral": "#e0e0e0",
}

STAGE_LINE_COLORS = {
    "Stage 2": "#2e7d32",
    "Stage 3": "#f9a825",
    "Stage 4": "#c62828",
    "Stage 1 / Neutral": "#757575",
}


# ── Data Preparation ─────────────────────────────────────────────────────────

def prepare(symbol: str) -> pd.DataFrame:
    """Download and prepare data with SMAs and stage classification."""
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="2y")
    if df.empty or len(df) < 250:
        return pd.DataFrame()

    df["SMA10"] = df["Close"].rolling(10).mean()
    df["SMA20"] = df["Close"].rolling(20).mean()
    df["SMA50"] = df["Close"].rolling(50).mean()
    df["SMA150"] = df["Close"].rolling(150).mean()
    df["SMA200"] = df["Close"].rolling(200).mean()
    df["SMA200_prev20"] = df["SMA200"].shift(20)
    df["Vol_Avg50"] = df["Volume"].rolling(50).mean()
    df["Stage_Daily"] = classify_stage_vectorized(df)
    df["Score"] = calculate_score_vectorized(df)
    df["Short_Score"] = calculate_short_score_vectorized(df)

    df = df.dropna(subset=["SMA200", "SMA200_prev20"])

    # Trim to last ~1y for the chart
    if len(df) > 252:
        df = df.iloc[-252:]

    return df


# ── AI Highlights ─────────────────────────────────────────────────────────────

def generate_ai_highlights(results_df: pd.DataFrame) -> str:
    """Use Claude Haiku to generate brief market highlights from screener results."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, skipping AI highlights")
        return ""

    today = datetime.now().strftime("%Y-%m-%d")
    table = results_df.to_string(index=False)

    prompt = f"""You are a concise market analyst. Analyze these ETF screener results from {today} and write 3-5 brief highlights in Spanish.

Focus on:
- Overall market regime (how many ETFs in uptrend vs neutral/downtrend)
- Sector rotation signals (which sectors are strongest/weakest by RS Score)
- Notable signals (any ETF changing stage, unusual scores, divergences)
- Safe havens status (GLD, TLT, BTAL behavior)
- Risk appetite (IBIT, SMH, EEM vs defensive XLU, XLP)

Keep it simple and actionable. Use bullet points. No more than 150 words total.

Scoring guide:
- Stage 2 = Uptrend, Stage 4 = Downtrend, Stage 1 = Neutral, Stage 3 = Distribution
- Score 1-2 = Buy zone (near moving averages), 4 = Hold, 7+ = Extended, 11 = Not in position
- RS Score = Relative strength vs SPY (positive = outperforming, negative = underperforming)
- Trend Template PASS = All Minervini criteria met

Data:
{table}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        highlights = message.content[0].text
        logger.info("AI highlights generated successfully")
        return highlights
    except Exception as e:
        logger.error(f"Failed to generate AI highlights: {e}")
        return ""


# ── Analysis + Chart ─────────────────────────────────────────────────────────

def analyze_and_plot():
    """Analyze all ETFs and produce a grid of charts."""
    os.makedirs(os.path.join(PROJECT_ROOT, ".tmp"), exist_ok=True)

    logger.info("Fetching SPY benchmark for RS Score...")
    benchmark_df = yf.Ticker("SPY").history(period="2y")

    results = []
    data = {}

    for symbol in ETF_LIST:
        logger.info(f"Analyzing {symbol}...")
        df = prepare(symbol)
        if df.empty:
            logger.warning(f"  {symbol}: insufficient data, skipping")
            continue

        data[symbol] = df
        latest = df.iloc[-1]

        rs = calculate_rs_score(df, benchmark_df)
        trend = check_trend_template(df)

        results.append({
            "ETF": symbol,
            "Price": round(latest["Close"], 2),
            "Stage": latest["Stage_Daily"],
            "Score": int(latest["Score"]),
            "Short_Score": int(latest["Short_Score"]),
            "RS_Score": rs,
            "Trend_Template": trend["status"],
            "Abv50": "Y" if latest["Close"] > latest["SMA50"] else "N",
            "Abv200": "Y" if latest["Close"] > latest["SMA200"] else "N",
        })

    results_df = pd.DataFrame(results)

    # Print summary
    logger.info("\n" + "=" * 80)
    logger.info("ETF SCREENER RESULTS — Stage Analysis")
    logger.info("=" * 80)
    print(results_df.to_string(index=False))
    print()

    # ── Generate grid chart ──────────────────────────────────────────────
    symbols = list(data.keys())
    n = len(symbols)
    cols = 4
    rows = (n + cols - 1) // cols

    fig, axes = plt.subplots(rows, cols, figsize=(24, rows * 4.5))
    fig.suptitle(
        "ETF Universe — Stage Analysis Overview",
        fontsize=20, fontweight="bold", y=1.01,
    )

    axes_flat = axes.flatten() if n > cols else (axes if n > 1 else [axes])

    for idx, symbol in enumerate(symbols):
        ax = axes_flat[idx]
        df = data[symbol]
        dates = df.index
        latest = df.iloc[-1]
        stage = latest["Stage_Daily"]
        score = int(latest["Score"])
        price = latest["Close"]

        # Stage background shading
        prev_stage = df["Stage_Daily"].iloc[0]
        stage_start = dates[0]
        for i in range(1, len(df)):
            curr = df["Stage_Daily"].iloc[i]
            if curr != prev_stage or i == len(df) - 1:
                color = STAGE_COLORS.get(prev_stage, "#f5f5f5")
                ax.axvspan(stage_start, dates[i], alpha=0.3, color=color, linewidth=0)
                stage_start = dates[i]
                prev_stage = curr

        # Price + SMAs
        ax.plot(dates, df["Close"], color="black", linewidth=1.5, zorder=5)
        ax.plot(dates, df["SMA50"], color="#e65100", linewidth=1, alpha=0.8)
        ax.plot(dates, df["SMA200"], color="#c62828", linewidth=1.2, alpha=0.8)

        # Current price label
        ax.annotate(
            f"${price:.2f}",
            xy=(dates[-1], price),
            fontsize=8, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.9),
        )

        # Title with stage color
        stage_color = STAGE_LINE_COLORS.get(stage, "#333")
        rs_val = next((r["RS_Score"] for r in results if r["ETF"] == symbol), 0)
        trend_val = next((r["Trend_Template"] for r in results if r["ETF"] == symbol), "N/A")

        trend_icon = "PASS" if trend_val == "PASS" else "FAIL"
        trend_color = "#2e7d32" if trend_val == "PASS" else "#c62828"

        ax.set_title(
            f"{symbol}  |  {stage}  |  Score: {score}  |  RS: {rs_val:+.1f}",
            fontsize=10, fontweight="bold", color=stage_color, pad=8,
        )

        # Trend template badge
        ax.text(
            0.98, 0.95, trend_icon, transform=ax.transAxes,
            fontsize=8, fontweight="bold", color="white",
            ha="right", va="top",
            bbox=dict(boxstyle="round,pad=0.3", facecolor=trend_color, alpha=0.85),
        )

        ax.grid(True, alpha=0.15)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax.tick_params(axis="both", labelsize=8)

    # Hide unused subplots
    for idx in range(n, len(axes_flat)):
        axes_flat[idx].set_visible(False)

    # Legend
    stage_patches = [
        Patch(facecolor="#c8e6c9", alpha=0.5, label="Stage 2 (Uptrend)"),
        Patch(facecolor="#fff9c4", alpha=0.5, label="Stage 3 (Distribution)"),
        Patch(facecolor="#ffcdd2", alpha=0.5, label="Stage 4 (Downtrend)"),
        Patch(facecolor="#e0e0e0", alpha=0.5, label="Stage 1 (Neutral)"),
    ]
    fig.legend(
        handles=stage_patches, loc="lower center", ncol=4,
        fontsize=11, frameon=True, bbox_to_anchor=(0.5, -0.02),
    )

    plt.tight_layout()
    path = os.path.join(PROJECT_ROOT, ".tmp", "etf_screener_grid.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    logger.info(f"\nChart saved to: {path}")

    return path, results_df


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(results_df: pd.DataFrame, chart_path: str, ai_highlights: str = ""):
    """Send ETF screener results via Gmail."""
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_pass:
        logger.warning("GMAIL_USER / GMAIL_APP_PASSWORD not set, skipping email")
        return

    today = datetime.now().strftime("%Y-%m-%d")

    # Count ETFs by stage
    stage_counts = results_df["Stage"].value_counts().to_dict()
    stage2_count = stage_counts.get("Stage 2", 0)
    total = len(results_df)

    # Build buy candidates (Stage 2, Score <=4, Trend PASS)
    buy_mask = (
        (results_df["Stage"] == "Stage 2")
        & (results_df["Score"] <= 4)
        & (results_df["Trend_Template"] == "PASS")
    )
    buy_candidates = results_df[buy_mask].sort_values("RS_Score", ascending=False)

    # ── HTML ──────────────────────────────────────────────────────────────
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="font-family: Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px;">

    <div style="background: linear-gradient(135deg, #1a237e 0%, #283593 50%, #3949ab 100%);
                color: white; padding: 20px; border-radius: 10px; margin-bottom: 20px;">
        <h1 style="margin: 0;">ETF Screener — Stage Analysis</h1>
        <p style="margin: 5px 0 0 0; opacity: 0.8;">
            {today} | {total} ETFs analyzed | {stage2_count} in Stage 2
        </p>
    </div>
    """

    # AI Highlights section
    if ai_highlights:
        # Convert markdown bullet points to HTML
        highlights_html = ai_highlights.replace("\n", "<br>")
        html += f"""
        <div style="background: #f3e5f5; border-left: 4px solid #7b1fa2; padding: 15px 20px;
                    border-radius: 0 8px 8px 0; margin-bottom: 20px;">
            <h3 style="margin: 0 0 10px 0; color: #7b1fa2;">AI Market Highlights</h3>
            <div style="font-size: 14px; line-height: 1.6; color: #333;">
                {highlights_html}
            </div>
        </div>
        """

    # Buy candidates section
    if not buy_candidates.empty:
        html += """
        <h2 style="color: #2e7d32; border-bottom: 2px solid #2e7d32; padding-bottom: 5px;">
            Buy Zone (Stage 2 + Score &le; 4 + Trend PASS)
        </h2>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 13px;">
        <tr style="background: #2e7d32; color: white;">
            <th style="padding: 8px; text-align: left;">ETF</th>
            <th style="padding: 8px; text-align: right;">Price</th>
            <th style="padding: 8px; text-align: center;">Score</th>
            <th style="padding: 8px; text-align: right;">RS Score</th>
            <th style="padding: 8px; text-align: center;">Abv50</th>
            <th style="padding: 8px; text-align: center;">Abv200</th>
        </tr>
        """
        for _, row in buy_candidates.iterrows():
            html += f"""
            <tr style="border-bottom: 1px solid #e0e0e0;">
                <td style="padding: 8px; font-weight: bold;">{row['ETF']}</td>
                <td style="padding: 8px; text-align: right;">${row['Price']:.2f}</td>
                <td style="padding: 8px; text-align: center;
                    color: {'#2e7d32' if row['Score'] <= 2 else '#e65100'};">
                    {row['Score']}</td>
                <td style="padding: 8px; text-align: right;
                    color: {'#2e7d32' if row['RS_Score'] > 0 else '#c62828'};">
                    {row['RS_Score']:+.1f}</td>
                <td style="padding: 8px; text-align: center;">{row['Abv50']}</td>
                <td style="padding: 8px; text-align: center;">{row['Abv200']}</td>
            </tr>
            """
        html += "</table>"

    # Full results table
    html += """
    <h2 style="color: #1a237e; border-bottom: 2px solid #1a237e; padding-bottom: 5px;">
        Full ETF Universe
    </h2>
    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 13px;">
    <tr style="background: #1a237e; color: white;">
        <th style="padding: 8px; text-align: left;">ETF</th>
        <th style="padding: 8px; text-align: right;">Price</th>
        <th style="padding: 8px; text-align: center;">Stage</th>
        <th style="padding: 8px; text-align: center;">Score</th>
        <th style="padding: 8px; text-align: right;">RS Score</th>
        <th style="padding: 8px; text-align: center;">Trend</th>
        <th style="padding: 8px; text-align: center;">Abv50</th>
        <th style="padding: 8px; text-align: center;">Abv200</th>
    </tr>
    """

    for _, row in results_df.sort_values("RS_Score", ascending=False).iterrows():
        stage = row["Stage"]
        if stage == "Stage 2":
            stage_bg = "#e8f5e9"
        elif stage == "Stage 4":
            stage_bg = "#ffebee"
        elif stage == "Stage 3":
            stage_bg = "#fffde7"
        else:
            stage_bg = "#f5f5f5"

        trend_badge = (
            '<span style="background:#2e7d32;color:white;padding:2px 6px;border-radius:3px;font-size:11px;">PASS</span>'
            if row["Trend_Template"] == "PASS"
            else '<span style="background:#c62828;color:white;padding:2px 6px;border-radius:3px;font-size:11px;">FAIL</span>'
        )

        html += f"""
        <tr style="border-bottom: 1px solid #e0e0e0; background: {stage_bg};">
            <td style="padding: 8px; font-weight: bold;">{row['ETF']}</td>
            <td style="padding: 8px; text-align: right;">${row['Price']:.2f}</td>
            <td style="padding: 8px; text-align: center;">{stage}</td>
            <td style="padding: 8px; text-align: center;">{row['Score']}</td>
            <td style="padding: 8px; text-align: right;
                color: {'#2e7d32' if row['RS_Score'] > 0 else '#c62828'};">
                {row['RS_Score']:+.1f}</td>
            <td style="padding: 8px; text-align: center;">{trend_badge}</td>
            <td style="padding: 8px; text-align: center;">{row['Abv50']}</td>
            <td style="padding: 8px; text-align: center;">{row['Abv200']}</td>
        </tr>
        """

    html += "</table>"

    # Chart
    html += """
    <h2 style="color: #1a237e; border-bottom: 2px solid #1a237e; padding-bottom: 5px;">
        Charts
    </h2>
    <img src="cid:etf_grid_chart" style="width: 100%; border-radius: 8px;">
    """

    # Stage legend
    html += """
    <div style="margin-top: 20px; padding: 15px; background: #f5f5f5; border-radius: 8px; font-size: 12px; color: #666;">
        <strong>Stage Legend:</strong>
        <span style="background:#c8e6c9;padding:2px 8px;border-radius:3px;">Stage 2 = Uptrend</span>
        <span style="background:#fff9c4;padding:2px 8px;border-radius:3px;">Stage 3 = Distribution</span>
        <span style="background:#ffcdd2;padding:2px 8px;border-radius:3px;">Stage 4 = Downtrend</span>
        <span style="background:#e0e0e0;padding:2px 8px;border-radius:3px;">Stage 1 = Neutral</span>
        &nbsp;|&nbsp;
        <strong>Score:</strong> 1-2 = Buy zone, 4 = Hold, 7+ = Extended, 11 = Danger
    </div>
    </body></html>
    """

    # ── Build MIME message ────────────────────────────────────────────────
    try:
        msg = MIMEMultipart("related")
        msg["Subject"] = f"ETF Screener — {today} | {stage2_count}/{total} Stage 2"
        msg["From"] = gmail_user
        msg["To"] = ", ".join(RECIPIENTS)

        msg.attach(MIMEText(html, "html"))

        # Attach grid chart
        if chart_path and os.path.exists(chart_path):
            with open(chart_path, "rb") as img_file:
                img = MIMEImage(img_file.read(), _subtype="png")
                img.add_header("Content-ID", "<etf_grid_chart>")
                img.add_header("Content-Disposition", "inline",
                               filename="etf_screener_grid.png")
                msg.attach(img)

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, RECIPIENTS, msg.as_string())

        logger.info(f"Email sent to {len(RECIPIENTS)} recipient(s)")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily ETF Screener")
    parser.add_argument("--no-email", action="store_true",
                        help="Skip sending email")
    args = parser.parse_args()

    chart_path, results_df = analyze_and_plot()

    # Generate AI highlights
    ai_highlights = generate_ai_highlights(results_df)
    if ai_highlights:
        print("\n--- AI Highlights ---")
        print(ai_highlights)
        print("---\n")

    if not args.no_email:
        send_email(results_df, chart_path, ai_highlights)
    else:
        logger.info("--no-email flag set, skipping email")

    print(f"\nDone! Chart: {chart_path}")

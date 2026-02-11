"""
Daily Portfolio Tracker — Hybrid System (Long + Short).

Long Algorithm:
  - Entry: Stage 2 + Score <= 4 + Trend Template PASS, ranked by RS Score, max 15 slots
  - Monthly Exit: Last trading day of month, exit if Stage != Stage 2
  - Daily Emergency: Exit if P&L <= -15% from entry OR Stage == Stage 4

Short Algorithm (NDX100):
  - Entry: Stage 4 + Short_Score <= 7 (bear flag), max 10 slots
  - Exit: Stage reversal (Stage 2 or Stage 1) OR 40-day time stop

Outputs:
  - Google Sheets (6 tabs: Portfolio, Short_Portfolio, Candidates, Short_Candidates, Trade Log, Equity)
  - Daily email summary with inline stock charts (2 long + 2 short)

Usage:
  python tools/portfolio_tracker.py              # Full run
  python tools/portfolio_tracker.py --dry-run    # No state save, no email/sheets
  python tools/portfolio_tracker.py --force-rebalance  # Force monthly rebalance check
"""

import argparse
import base64
import json
import logging
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Add project root to path for src imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.screener import analyze_ticker, get_sp500_tickers
from tools.fetch_index_tickers import fetch_nasdaq100

try:
    from src.sheets_exporter import export_to_sheets
    SHEETS_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    SHEETS_AVAILABLE = False

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
MAX_SLOTS = 15
MAX_SHORT_SLOTS = 10
SHORT_TIME_STOP = 40  # Max trading days in a short position
EMERGENCY_STOP_PCT = -15.0
STATE_FILE = os.path.join(PROJECT_ROOT, ".tmp", "portfolio_state.json")
TOP100_FILE = os.path.join(PROJECT_ROOT, ".tmp", "top100_tickers.txt")


# ── State Management ────────────────────────────────────────────────────────

def _get_sheet_connection():
    """Get gspread spreadsheet object (shared helper)."""
    creds_b64 = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    sheet_id = os.environ.get("TRACKER_SHEET_ID")
    if not creds_b64 or not sheet_id:
        return None
    creds_dict = json.loads(base64.b64decode(creds_b64))
    from src.sheets_exporter import get_gspread_client
    gc = get_gspread_client(creds_dict)
    return gc.open_by_key(sheet_id)


def load_state() -> dict:
    """Load portfolio state from Google Sheets 'state' tab, fallback to local file."""
    # Try Google Sheets first
    try:
        sh = _get_sheet_connection()
        if sh:
            ws = sh.worksheet("state")
            raw = ws.acell("A1").value
            if raw:
                state = json.loads(raw)
                logger.info(f"State loaded from Google Sheets (long: {len(state.get('holdings', {}))}, short: {len(state.get('short_holdings', {}))})")
                # Ensure short_holdings key exists for older states
                if "short_holdings" not in state:
                    state["short_holdings"] = {}
                return state
    except Exception as e:
        logger.warning(f"Could not load state from Sheets: {e}")

    # Fallback to local file
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                if "short_holdings" not in state:
                    state["short_holdings"] = {}
                return state
        except Exception as e:
            logger.error(f"Error loading local state: {e}")
    return {"last_run": None, "holdings": {}, "short_holdings": {}, "trade_log": [],
            "equity": 100.0, "prev_prices": {}, "equity_history": []}


def save_state(state: dict):
    """Save portfolio state to Google Sheets 'state' tab + local file."""
    state_json = json.dumps(state, indent=2, default=str)

    # Save to Google Sheets
    try:
        sh = _get_sheet_connection()
        if sh:
            try:
                ws = sh.worksheet("state")
            except Exception:
                ws = sh.add_worksheet(title="state", rows="2", cols="1")
            ws.update_acell("A1", state_json)
            logger.info("State saved to Google Sheets")
    except Exception as e:
        logger.warning(f"Could not save state to Sheets: {e}")

    # Also save locally as backup
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write(state_json)
    logger.info(f"State saved to {STATE_FILE}")


# ── Universe ─────────────────────────────────────────────────────────────────

def get_universe() -> list:
    """Get the long ticker universe (top 100 S&P 500 by market cap)."""
    if os.path.exists(TOP100_FILE):
        with open(TOP100_FILE, "r") as f:
            tickers = [line.strip() for line in f if line.strip()]
        logger.info(f"Loaded {len(tickers)} tickers from {TOP100_FILE}")
        return tickers
    # Fallback to full S&P 500
    logger.info("Top 100 file not found, fetching full S&P 500...")
    return get_sp500_tickers()


def get_ndx100_tickers() -> list:
    """Get Nasdaq 100 tickers for short universe."""
    try:
        ndx = fetch_nasdaq100()
        return ndx["Ticker"].tolist()
    except Exception as e:
        logger.warning(f"Could not fetch NDX100: {e}")
        return []


# ── Monthly Rebalance Detection ──────────────────────────────────────────────

def is_last_trading_day_of_month(date=None) -> bool:
    """Check if today is the last trading day of the month."""
    if date is None:
        date = pd.Timestamp.now()
    else:
        date = pd.Timestamp(date)
    next_bday = date + pd.tseries.offsets.BDay(1)
    return next_bday.month != date.month


# ── Equity Tracking ──────────────────────────────────────────────────────────

def update_equity(state, holdings_data):
    """Update portfolio equity based on daily price changes.

    Uses equal-weight model: each of MAX_SLOTS contributes 1/MAX_SLOTS.
    Empty slots = cash (0% return).
    """
    equity = state.get("equity", 100.0)
    prev_prices = state.get("prev_prices", {})
    equity_history = state.get("equity_history", [])

    # Calculate daily return from price changes
    daily_return = 0.0
    current_prices = {}

    for h in holdings_data:
        ticker = h["Ticker"]
        current_price = h.get("Current_Price", 0)
        if current_price <= 0:
            continue
        current_prices[ticker] = current_price

        if ticker in prev_prices and prev_prices[ticker] > 0:
            slot_ret = (current_price - prev_prices[ticker]) / prev_prices[ticker]
        else:
            entry_price = h.get("Entry_Price", current_price)
            if entry_price > 0:
                slot_ret = (current_price - entry_price) / entry_price
            else:
                slot_ret = 0
        daily_return += slot_ret / MAX_SLOTS

    # Update equity
    equity *= (1 + daily_return)

    # Record history
    today = datetime.now().strftime("%Y-%m-%d")
    equity_history.append({"date": today, "equity": round(equity, 2)})

    # Update state
    state["equity"] = round(equity, 4)
    state["prev_prices"] = current_prices
    state["equity_history"] = equity_history

    return equity


def calculate_ytd(state):
    """Calculate Year-to-Date return from equity history."""
    history = state.get("equity_history", [])
    if not history:
        return 0.0

    current_year = datetime.now().year
    jan1_equity = None

    for entry in history:
        entry_date = entry["date"]
        if entry_date.startswith(str(current_year)):
            jan1_equity = entry["equity"]
            break

    if jan1_equity is None or jan1_equity <= 0:
        jan1_equity = history[0]["equity"]

    current_equity = state.get("equity", 100.0)
    ytd_pct = (current_equity / jan1_equity - 1) * 100
    return round(ytd_pct, 2)


# ── Chart Generation ─────────────────────────────────────────────────────────

def generate_stock_chart(ticker: str, entry_price: float = None, side: str = "long") -> str:
    """Generate a simple stock chart with SMAs. Returns path to saved PNG or None."""
    try:
        df = yf.Ticker(ticker).history(period="6mo")
        if df.empty or len(df) < 20:
            return None

        df["SMA10"] = df["Close"].rolling(10).mean()
        df["SMA20"] = df["Close"].rolling(20).mean()
        df["SMA50"] = df["Close"].rolling(50).mean()

        fig, ax = plt.subplots(figsize=(10, 4.5))
        dates = df.index

        ax.plot(dates, df["Close"], color="black", linewidth=1.5, label="Price")
        ax.plot(dates, df["SMA10"], color="#42a5f5", linewidth=0.8, alpha=0.7, label="SMA10")
        ax.plot(dates, df["SMA20"], color="#1565c0", linewidth=1, alpha=0.8, label="SMA20")
        ax.plot(dates, df["SMA50"], color="#e65100", linewidth=1.2, label="SMA50")

        if entry_price:
            color = "#2e7d32" if side == "long" else "#d32f2f"
            label_txt = "Entry" if side == "long" else "Short Entry"
            ax.axhline(y=entry_price, color=color, linestyle="--", alpha=0.7, linewidth=1.2, label=label_txt)

        # Current price annotation
        last_price = df["Close"].iloc[-1]
        ax.annotate(
            f"${last_price:.2f}", xy=(dates[-1], last_price),
            fontsize=9, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8),
        )

        side_label = "LONG" if side == "long" else "SHORT"
        title_color = "#2e7d32" if side == "long" else "#d32f2f"
        if entry_price:
            if side == "long":
                pnl = (last_price - entry_price) / entry_price * 100
            else:
                pnl = (entry_price - last_price) / entry_price * 100
            ax.set_title(f"{ticker} {side_label}  |  P&L: {pnl:+.1f}%",
                         fontsize=14, fontweight="bold", color=title_color)
        else:
            ax.set_title(f"{ticker} {side_label}", fontsize=14, fontweight="bold", color=title_color)

        ax.legend(loc="upper left", fontsize=9)
        ax.grid(True, alpha=0.2)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        ax.set_ylabel("Price ($)", fontsize=10)

        plt.tight_layout()
        path = os.path.join(PROJECT_ROOT, ".tmp", f"chart_{side}_{ticker}.png")
        fig.savefig(path, dpi=120, bbox_inches="tight")
        plt.close()
        return path
    except Exception as e:
        logger.warning(f"Could not generate chart for {ticker}: {e}")
        return None


# ── Core Logic ───────────────────────────────────────────────────────────────

def run_tracker(dry_run: bool = False, force_rebalance: bool = False):
    """Main tracker execution."""
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"=== Portfolio Tracker — {today} ===")

    state = load_state()
    holdings = state["holdings"]
    short_holdings = state.get("short_holdings", {})
    trade_log = state["trade_log"]

    # Check if it's a rebalance day
    rebalance_day = force_rebalance or is_last_trading_day_of_month()
    if rebalance_day:
        logger.info("REBALANCE DAY — Monthly exit check active")
    else:
        logger.info("Regular day — Emergency stops only")

    # ── Step 1: Fetch benchmark ──────────────────────────────────────────
    logger.info("Fetching SPY benchmark...")
    benchmark_df = yf.Ticker("SPY").history(period="2y")

    # ── Step 2: Gather all tickers and analyze once ──────────────────────
    long_universe = get_universe()
    ndx100_universe = get_ndx100_tickers()

    all_tickers = sorted(
        set(long_universe) | set(ndx100_universe)
        | set(holdings.keys()) | set(short_holdings.keys())
    )

    logger.info(f"Analyzing {len(all_tickers)} unique tickers...")
    scan_results = {}
    for i, ticker in enumerate(all_tickers):
        if (i + 1) % 25 == 0:
            logger.info(f"  Progress: {i + 1}/{len(all_tickers)}")
        result = analyze_ticker(ticker, benchmark_df)
        if result:
            scan_results[ticker] = result
    logger.info(f"Successfully analyzed {len(scan_results)} tickers")

    # ── Step 3: Process LONG holdings ────────────────────────────────────
    exits_today = []
    holdings_data = []

    if holdings:
        logger.info(f"Processing {len(holdings)} long holdings...")
        for ticker, pos in list(holdings.items()):
            result = scan_results.get(ticker)
            if result is None:
                logger.warning(f"Could not analyze {ticker}, keeping position")
                holdings_data.append({
                    "Ticker": ticker, "Entry_Date": pos["entry_date"],
                    "Entry_Price": pos["entry_price"], "Current_Price": 0,
                    "PnL_Pct": 0, "Stage": "N/A", "Score": 0,
                    "Trend": "N/A", "RS_Score": 0, "Alert": "DATA ERROR"
                })
                continue

            current_price = result["Price"]
            pnl_pct = round((current_price - pos["entry_price"]) / pos["entry_price"] * 100, 2)
            stage = result["Stage"]

            # Build alert flags
            alerts = []
            if result["Trend_Template"] == "FAIL":
                alerts.append("TREND FAIL")
            if stage != "Stage 2":
                alerts.append(f"STAGE: {stage}")

            holdings_data.append({
                "Ticker": ticker,
                "Entry_Date": pos["entry_date"],
                "Entry_Price": pos["entry_price"],
                "Current_Price": current_price,
                "PnL_Pct": pnl_pct,
                "Stage": stage,
                "Score": result["Score"],
                "Trend": result["Trend_Template"],
                "RS_Score": result["RS_Score"],
                "Alert": " | ".join(alerts) if alerts else ""
            })

            # ── Daily Emergency Check ────────────────────────────────────
            exit_reason = None

            if pnl_pct <= EMERGENCY_STOP_PCT:
                exit_reason = f"Emergency Stop ({pnl_pct:.1f}%)"
            elif stage == "Stage 4":
                exit_reason = "Emergency Stop (Stage 4)"

            # ── Monthly Rebalance Check ──────────────────────────────────
            if rebalance_day and exit_reason is None:
                if stage != "Stage 2":
                    exit_reason = f"Monthly Rebalance ({stage})"

            if exit_reason:
                logger.info(f"EXIT: {ticker} — {exit_reason} (P&L: {pnl_pct:+.1f}%)")
                trade_log.append({
                    "ticker": ticker,
                    "side": "LONG",
                    "entry_date": pos["entry_date"],
                    "entry_price": pos["entry_price"],
                    "exit_date": today,
                    "exit_price": current_price,
                    "return_pct": pnl_pct,
                    "exit_reason": exit_reason,
                })
                exits_today.append({
                    "Ticker": ticker, "Reason": exit_reason,
                    "Entry_Price": pos["entry_price"],
                    "Exit_Price": current_price, "Return_Pct": pnl_pct
                })
                del holdings[ticker]

    # ── Step 4: Process SHORT holdings ───────────────────────────────────
    short_exits_today = []
    short_holdings_data = []

    if short_holdings:
        logger.info(f"Processing {len(short_holdings)} short holdings...")
        for ticker, pos in list(short_holdings.items()):
            result = scan_results.get(ticker)
            if result is None:
                logger.warning(f"Could not analyze short {ticker}, keeping position")
                short_holdings_data.append({
                    "Ticker": ticker, "Entry_Date": pos["entry_date"],
                    "Entry_Price": pos["entry_price"], "Current_Price": 0,
                    "PnL_Pct": 0, "Stage": "N/A", "Score": 0,
                    "Trend": "N/A", "RS_Score": 0, "Alert": "DATA ERROR"
                })
                continue

            current_price = result["Price"]
            # PnL inverted for shorts: profit when price drops
            pnl_pct = round((pos["entry_price"] - current_price) / pos["entry_price"] * 100, 2)
            stage = result["Stage"]
            days_held = pos.get("days_held", 0) + 1
            short_holdings[ticker]["days_held"] = days_held

            # Build alert flags for shorts
            alerts = []
            if stage in ("Stage 2", "Stage 1 / Neutral"):
                alerts.append("EXIT: Stage reversal")
            if days_held >= SHORT_TIME_STOP:
                alerts.append(f"TIME STOP ({days_held}d)")

            short_holdings_data.append({
                "Ticker": ticker,
                "Entry_Date": pos["entry_date"],
                "Entry_Price": pos["entry_price"],
                "Current_Price": current_price,
                "PnL_Pct": pnl_pct,
                "Stage": stage,
                "Score": result["Short_Score"],
                "Trend": result["Trend_Template"],
                "RS_Score": result["RS_Score"],
                "Alert": " | ".join(alerts) if alerts else ""
            })

            # Short exit logic
            exit_reason = None
            if stage in ("Stage 2", "Stage 1 / Neutral"):
                exit_reason = f"Stage Reversal ({stage})"
            elif days_held >= SHORT_TIME_STOP:
                exit_reason = f"Time Stop ({days_held}d)"

            if exit_reason:
                logger.info(f"SHORT EXIT: {ticker} — {exit_reason} (P&L: {pnl_pct:+.1f}%)")
                trade_log.append({
                    "ticker": ticker,
                    "side": "SHORT",
                    "entry_date": pos["entry_date"],
                    "entry_price": pos["entry_price"],
                    "exit_date": today,
                    "exit_price": current_price,
                    "return_pct": pnl_pct,
                    "exit_reason": exit_reason,
                })
                short_exits_today.append({
                    "Ticker": ticker, "Reason": exit_reason,
                    "Entry_Price": pos["entry_price"],
                    "Exit_Price": current_price, "Return_Pct": pnl_pct
                })
                del short_holdings[ticker]

    # ── Step 5: Find LONG candidates ─────────────────────────────────────
    open_slots = MAX_SLOTS - len(holdings)
    logger.info(f"Long open slots: {open_slots}/{MAX_SLOTS}")

    candidates = []
    entries_today = []

    for ticker in long_universe:
        if ticker in holdings:
            continue
        result = scan_results.get(ticker)
        if result is None:
            continue
        # Entry criteria: Stage 2 + Score <= 4 + Trend PASS
        if (result["Stage"] == "Stage 2"
                and result["Score"] <= 4
                and result["Trend_Template"] == "PASS"):
            candidates.append(result)

    # Rank by RS Score descending
    candidates.sort(key=lambda x: x["RS_Score"], reverse=True)
    logger.info(f"Found {len(candidates)} long candidates")

    # ── Step 6: Find SHORT candidates (NDX100) ──────────────────────────
    short_open_slots = MAX_SHORT_SLOTS - len(short_holdings)
    logger.info(f"Short open slots: {short_open_slots}/{MAX_SHORT_SLOTS}")

    short_candidates = []
    short_entries_today = []

    for ticker in ndx100_universe:
        if ticker in short_holdings:
            continue
        result = scan_results.get(ticker)
        if result is None:
            continue
        # Short entry: Stage 4 + Short_Score <= 7 (bear flag)
        if result["Stage"] == "Stage 4" and result["Short_Score"] <= 7:
            short_candidates.append(result)

    # Rank: best short entries first (low Short_Score), then weakest RS
    short_candidates.sort(key=lambda x: (x["Short_Score"], x["RS_Score"]))
    logger.info(f"Found {len(short_candidates)} short candidates")

    # ── Step 7: Fill LONG slots ──────────────────────────────────────────
    if open_slots > 0 and candidates:
        fills = candidates[:open_slots]
        for c in fills:
            ticker = c["Ticker"]
            holdings[ticker] = {
                "entry_date": today,
                "entry_price": c["Price"],
            }
            entries_today.append({
                "Ticker": ticker, "Price": c["Price"],
                "Score": c["Score"], "RS_Score": c["RS_Score"],
                "Sector": c["Sector"]
            })
            logger.info(f"ENTRY: {ticker} @ ${c['Price']:.2f} (RS: {c['RS_Score']}, Score: {c['Score']})")

    # ── Step 8: Fill SHORT slots ─────────────────────────────────────────
    if short_open_slots > 0 and short_candidates:
        fills = short_candidates[:short_open_slots]
        for c in fills:
            ticker = c["Ticker"]
            short_holdings[ticker] = {
                "entry_date": today,
                "entry_price": c["Price"],
                "days_held": 0,
            }
            short_entries_today.append({
                "Ticker": ticker, "Price": c["Price"],
                "Short_Score": c["Short_Score"], "RS_Score": c["RS_Score"],
                "Sector": c.get("Sector", "")
            })
            logger.info(f"SHORT ENTRY: {ticker} @ ${c['Price']:.2f} (SS: {c['Short_Score']}, RS: {c['RS_Score']})")

    # ── Step 9: Update equity (long portfolio) ───────────────────────────
    active_holdings_data = [h for h in holdings_data if h["Ticker"] in holdings]
    current_equity = update_equity(state, active_holdings_data)
    ytd_pct = calculate_ytd(state)

    active_pnls = [h["PnL_Pct"] for h in active_holdings_data if h.get("Current_Price", 0) > 0]
    avg_pnl = sum(active_pnls) / len(active_pnls) if active_pnls else 0

    active_short_data = [h for h in short_holdings_data if h["Ticker"] in short_holdings]
    short_pnls = [h["PnL_Pct"] for h in active_short_data if h.get("Current_Price", 0) > 0]
    avg_short_pnl = sum(short_pnls) / len(short_pnls) if short_pnls else 0

    # ── Step 10: Generate stock charts (2 long + 2 short) ────────────────
    os.makedirs(os.path.join(PROJECT_ROOT, ".tmp"), exist_ok=True)
    chart_paths = []

    # Top 2 long holdings by PnL
    long_for_charts = sorted(
        [h for h in active_holdings_data if h.get("Current_Price", 0) > 0],
        key=lambda x: x["PnL_Pct"], reverse=True
    )[:2]
    for h in long_for_charts:
        path = generate_stock_chart(h["Ticker"], h["Entry_Price"], "long")
        if path:
            chart_paths.append(path)

    # Top 2 short holdings by PnL (fallback to new entries if no existing holdings)
    short_for_charts = sorted(
        [h for h in active_short_data if h.get("Current_Price", 0) > 0],
        key=lambda x: x["PnL_Pct"], reverse=True
    )[:2]
    if not short_for_charts and short_entries_today:
        # First day: use newly entered shorts
        for e in short_entries_today[:2]:
            path = generate_stock_chart(e["Ticker"], e["Price"], "short")
            if path:
                chart_paths.append(path)
    else:
        for h in short_for_charts:
            path = generate_stock_chart(h["Ticker"], h["Entry_Price"], "short")
            if path:
                chart_paths.append(path)

    # ── Step 11: Update state ────────────────────────────────────────────
    state["last_run"] = today
    state["holdings"] = holdings
    state["short_holdings"] = short_holdings
    state["trade_log"] = trade_log

    # ── Print Summary ────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  PORTFOLIO TRACKER — {today}")
    print(f"{'='*60}")
    print(f"  LONG:  {len(holdings)}/{MAX_SLOTS}")
    print(f"  SHORT: {len(short_holdings)}/{MAX_SHORT_SLOTS}")
    print(f"  Equity: {current_equity:.2f} (base 100)")
    print(f"  YTD: {ytd_pct:+.2f}%")
    print(f"  Avg P&L (long): {avg_pnl:+.1f}%")
    print(f"  Avg P&L (short): {avg_short_pnl:+.1f}%")
    print(f"  Rebalance Day: {'YES' if rebalance_day else 'No'}")

    if exits_today:
        print(f"\n  --- Long Exits ---")
        for e in exits_today:
            print(f"    {e['Ticker']:6s}  {e['Return_Pct']:+6.1f}%  ({e['Reason']})")

    if entries_today:
        print(f"\n  --- Long Entries ---")
        for e in entries_today:
            print(f"    {e['Ticker']:6s}  ${e['Price']:>8.2f}  RS: {e['RS_Score']:>6.2f}  Score: {e['Score']}")

    if short_exits_today:
        print(f"\n  --- Short Exits ---")
        for e in short_exits_today:
            print(f"    {e['Ticker']:6s}  {e['Return_Pct']:+6.1f}%  ({e['Reason']})")

    if short_entries_today:
        print(f"\n  --- Short Entries ---")
        for e in short_entries_today:
            print(f"    {e['Ticker']:6s}  ${e['Price']:>8.2f}  SS: {e['Short_Score']}  RS: {e['RS_Score']:.2f}")

    if holdings_data:
        print(f"\n  --- Long Portfolio ---")
        for h in sorted(holdings_data, key=lambda x: x["PnL_Pct"], reverse=True):
            alert = f"  ! {h['Alert']}" if h["Alert"] else ""
            print(f"    {h['Ticker']:6s}  {h['PnL_Pct']:+6.1f}%  {h['Stage']:18s}  Score: {h['Score']:>2}{alert}")

    if short_holdings_data:
        print(f"\n  --- Short Portfolio ---")
        for h in sorted(short_holdings_data, key=lambda x: x["PnL_Pct"], reverse=True):
            alert = f"  ! {h['Alert']}" if h["Alert"] else ""
            print(f"    {h['Ticker']:6s}  {h['PnL_Pct']:+6.1f}%  {h['Stage']:18s}  SS: {h['Score']:>2}{alert}")

    if candidates:
        remaining = [c for c in candidates if c["Ticker"] not in holdings]
        if remaining:
            print(f"\n  --- Long Waiting List (Top 5) ---")
            for c in remaining[:5]:
                print(f"    {c['Ticker']:6s}  ${c['Price']:>8.2f}  RS: {c['RS_Score']:>6.2f}  Score: {c['Score']}  {c['Sector']}")

    if short_candidates:
        remaining_s = [c for c in short_candidates if c["Ticker"] not in short_holdings]
        if remaining_s:
            print(f"\n  --- Short Waiting List (Top 5) ---")
            for c in remaining_s[:5]:
                print(f"    {c['Ticker']:6s}  ${c['Price']:>8.2f}  SS: {c['Short_Score']}  RS: {c['RS_Score']:.2f}  {c.get('Sector', '')}")

    print(f"{'='*60}\n")

    if dry_run:
        logger.info("DRY RUN — No state saved, no email/sheets sent")
        return

    # ── Step 12: Save state ──────────────────────────────────────────────
    save_state(state)

    # ── Step 13: Export to Google Sheets ──────────────────────────────────
    export_google_sheets(
        holdings_data, candidates, trade_log,
        state.get("equity_history", []), current_equity, ytd_pct,
        short_holdings_data=short_holdings_data,
        short_candidates=short_candidates,
    )

    # ── Step 14: Send daily email ────────────────────────────────────────
    send_tracker_email(
        holdings_data, exits_today, entries_today,
        candidates, rebalance_day, current_equity, ytd_pct,
        short_holdings_data=short_holdings_data,
        short_exits_today=short_exits_today,
        short_entries_today=short_entries_today,
        short_candidates=short_candidates,
        chart_paths=chart_paths,
    )


# ── Google Sheets Export ─────────────────────────────────────────────────────

def _get_or_create_worksheet(sh, name, rows=50, cols=20):
    """Get existing worksheet or create it. Clear if exists."""
    try:
        ws = sh.worksheet(name)
        ws.clear()
        return ws
    except Exception:
        return sh.add_worksheet(title=name, rows=str(rows), cols=str(cols))


def _upload_df(ws, df):
    """Upload a DataFrame to a worksheet."""
    from src.sheets_exporter import get_column_letter, apply_formatting
    header = [df.columns.values.tolist()]
    data = df.fillna("").values.tolist()
    max_row = len(data) + 1
    last_col = get_column_letter(len(df.columns) - 1)
    ws.update(values=header + data, range_name=f"A1:{last_col}{max_row}")
    try:
        apply_formatting(ws, df, max_row, last_col)
    except Exception:
        pass


def export_google_sheets(holdings_data, candidates, trade_log,
                         equity_history=None, current_equity=100, ytd_pct=0,
                         short_holdings_data=None, short_candidates=None):
    """Export tabs to Google Sheets using sheet ID."""
    if not SHEETS_AVAILABLE:
        logger.warning("gspread_formatting not installed, skipping Sheets export")
        return

    creds_b64 = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if not creds_b64:
        logger.warning("GOOGLE_SHEETS_CREDENTIALS not set, skipping Sheets export")
        return

    sheet_id = os.environ.get("TRACKER_SHEET_ID")
    if not sheet_id:
        logger.warning("TRACKER_SHEET_ID not set, skipping Sheets export")
        return

    try:
        creds_dict = json.loads(base64.b64decode(creds_b64))
    except Exception as e:
        logger.error(f"Failed to decode Google credentials: {e}")
        return

    try:
        from src.sheets_exporter import get_gspread_client
        gc = get_gspread_client(creds_dict)
        sh = gc.open_by_key(sheet_id)
    except Exception as e:
        logger.error(f"Failed to open spreadsheet: {e}")
        return

    col_order = ["Ticker", "Entry_Date", "Entry_Price", "Current_Price",
                  "PnL_Pct", "Stage", "Score", "Trend", "RS_Score", "Alert"]

    # Tab 1: Portfolio (Long)
    if holdings_data:
        df_portfolio = pd.DataFrame(holdings_data)
        df_portfolio = df_portfolio[[c for c in col_order if c in df_portfolio.columns]]
        ws = _get_or_create_worksheet(sh, "Portfolio", rows=len(df_portfolio) + 5)
        _upload_df(ws, df_portfolio)
        logger.info("Exported Portfolio tab to Sheets")

    # Tab 2: Short_Portfolio (same columns as Portfolio)
    if short_holdings_data:
        df_short = pd.DataFrame(short_holdings_data)
        df_short = df_short[[c for c in col_order if c in df_short.columns]]
        ws = _get_or_create_worksheet(sh, "Short_Portfolio", rows=len(df_short) + 5)
        _upload_df(ws, df_short)
        logger.info("Exported Short_Portfolio tab to Sheets")

    # Tab 3: Candidates (Long)
    if candidates:
        df_cand = pd.DataFrame(candidates)
        cand_cols = ["Ticker", "Price", "Score", "Stage", "Trend_Template",
                     "RS_Score", "Sector", "Risk_%", "Stop_Loss"]
        df_cand = df_cand[[c for c in cand_cols if c in df_cand.columns]]
        ws = _get_or_create_worksheet(sh, "Candidates", rows=len(df_cand) + 5)
        _upload_df(ws, df_cand)
        logger.info("Exported Candidates tab to Sheets")

    # Tab 4: Short_Candidates
    if short_candidates:
        df_sc = pd.DataFrame(short_candidates)
        sc_cols = ["Ticker", "Price", "Short_Score", "Stage", "Trend_Template",
                   "RS_Score", "Sector"]
        df_sc = df_sc[[c for c in sc_cols if c in df_sc.columns]]
        ws = _get_or_create_worksheet(sh, "Short_Candidates", rows=len(df_sc) + 5)
        _upload_df(ws, df_sc)
        logger.info("Exported Short_Candidates tab to Sheets")

    # Tab 5: Trade Log
    if trade_log:
        df_log = pd.DataFrame(trade_log)
        log_cols = ["ticker", "side", "entry_date", "exit_date", "entry_price",
                    "exit_price", "return_pct", "exit_reason"]
        df_log = df_log[[c for c in log_cols if c in df_log.columns]]
        col_names = {"ticker": "Ticker", "side": "Side", "entry_date": "Entry_Date",
                     "exit_date": "Exit_Date", "entry_price": "Entry_Price",
                     "exit_price": "Exit_Price", "return_pct": "Return_Pct",
                     "exit_reason": "Reason"}
        df_log = df_log.rename(columns=col_names)
        ws = _get_or_create_worksheet(sh, "Trade_Log", rows=len(df_log) + 5)
        _upload_df(ws, df_log)
        logger.info("Exported Trade Log tab to Sheets")

    # Tab 6: Equity Curve
    if equity_history:
        df_eq = pd.DataFrame(equity_history)
        df_eq.columns = ["Date", "Equity"]
        df_eq["Return_%"] = ((df_eq["Equity"] / 100) - 1) * 100
        df_eq["Return_%"] = df_eq["Return_%"].round(2)
        # Add summary row
        summary_row = pd.DataFrame([{
            "Date": f"YTD: {ytd_pct:+.2f}%",
            "Equity": current_equity,
            "Return_%": df_eq["Return_%"].iloc[-1] if len(df_eq) > 0 else 0
        }])
        df_eq = pd.concat([summary_row, df_eq], ignore_index=True)
        ws = _get_or_create_worksheet(sh, "Equity", rows=len(df_eq) + 5, cols=5)
        header = [df_eq.columns.values.tolist()]
        data = df_eq.fillna("").values.tolist()
        ws.update(values=header + data, range_name=f"A1:C{len(data) + 1}")
        logger.info("Exported Equity tab to Sheets")


# ── Email ────────────────────────────────────────────────────────────────────

def _get_recipients_from_sheet():
    """Read email recipients from the 'mails' tab in the tracker Sheet."""
    try:
        creds_b64 = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
        sheet_id = os.environ.get("TRACKER_SHEET_ID")
        if not creds_b64 or not sheet_id:
            return []
        creds_dict = json.loads(base64.b64decode(creds_b64))
        from src.sheets_exporter import get_gspread_client
        gc = get_gspread_client(creds_dict)
        sh = gc.open_by_key(sheet_id)
        ws = sh.worksheet("mails")
        values = ws.col_values(1)  # Column A
        emails = [v.strip() for v in values if v.strip() and "@" in v]
        logger.info(f"Loaded {len(emails)} recipients from 'mails' tab")
        return emails
    except Exception as e:
        logger.warning(f"Could not read recipients from Sheet: {e}")
        return []


def send_tracker_email(holdings_data, exits_today, entries_today, candidates,
                       rebalance_day, current_equity=100, ytd_pct=0,
                       short_holdings_data=None, short_exits_today=None,
                       short_entries_today=None, short_candidates=None,
                       chart_paths=None):
    """Send daily summary email with long + short portfolios and charts."""
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")

    # Read recipients from Google Sheet 'mails' tab
    recipients = _get_recipients_from_sheet()
    if not recipients:
        # Fallback to .env
        recipients = [r.strip() for r in os.environ.get("EMAIL_RECIPIENTS", "").split(",") if r.strip()]

    if not gmail_user or not gmail_pass or not recipients:
        logger.warning("Email credentials not configured, skipping email")
        return

    short_holdings_data = short_holdings_data or []
    short_exits_today = short_exits_today or []
    short_entries_today = short_entries_today or []
    short_candidates = short_candidates or []
    chart_paths = chart_paths or []

    today = datetime.now().strftime("%Y-%m-%d")
    total_long = len([h for h in holdings_data if h.get("Current_Price", 0) > 0])
    total_short = len([h for h in short_holdings_data if h.get("Current_Price", 0) > 0])

    # Build HTML email
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="font-family: Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px;">

    <div style="background: linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%);
                color: white; padding: 20px; border-radius: 10px; margin-bottom: 20px;">
        <h1 style="margin: 0;">Portfolio Tracker</h1>
        <p style="margin: 5px 0 0 0; opacity: 0.8;">
            {today} | Long: {total_long}/{MAX_SLOTS} | Short: {total_short}/{MAX_SHORT_SLOTS}
            {' | REBALANCE DAY' if rebalance_day else ''}
        </p>
        <div style="display: flex; gap: 30px; margin-top: 12px;">
            <div style="background: rgba(255,255,255,0.15); padding: 10px 18px; border-radius: 6px;">
                <div style="font-size: 11px; opacity: 0.7;">Equity</div>
                <div style="font-size: 20px; font-weight: bold;">{current_equity:.2f}</div>
            </div>
            <div style="background: rgba(255,255,255,0.15); padding: 10px 18px; border-radius: 6px;">
                <div style="font-size: 11px; opacity: 0.7;">YTD</div>
                <div style="font-size: 20px; font-weight: bold; color: {'#4ade80' if ytd_pct >= 0 else '#f87171'};">
                    {ytd_pct:+.2f}%</div>
            </div>
            <div style="background: rgba(255,255,255,0.15); padding: 10px 18px; border-radius: 6px;">
                <div style="font-size: 11px; opacity: 0.7;">Total Return</div>
                <div style="font-size: 20px; font-weight: bold; color: {'#4ade80' if current_equity >= 100 else '#f87171'};">
                    {current_equity - 100:+.2f}%</div>
            </div>
        </div>
    </div>
    """

    # Section: Today's Actions (Long + Short combined)
    has_actions = exits_today or entries_today or short_exits_today or short_entries_today
    if has_actions:
        html += """
        <div style="background-color: #fff3cd; padding: 15px; border-radius: 8px;
                    margin-bottom: 20px; border-left: 4px solid #ffc107;">
            <h3 style="margin: 0 0 10px 0; color: #856404;">Today's Actions</h3>
        """
        if exits_today:
            html += "<p><strong>Long Exits:</strong></p><ul>"
            for e in exits_today:
                color = "#27ae60" if e["Return_Pct"] > 0 else "#e74c3c"
                html += f'<li><strong>{e["Ticker"]}</strong> — {e["Reason"]} '
                html += f'(<span style="color:{color}">{e["Return_Pct"]:+.1f}%</span>)</li>'
            html += "</ul>"
        if entries_today:
            html += "<p><strong>Long Entries:</strong></p><ul>"
            for e in entries_today:
                html += f'<li><strong>{e["Ticker"]}</strong> @ ${e["Price"]:.2f} '
                html += f'(RS: {e["RS_Score"]}, Score: {e["Score"]})</li>'
            html += "</ul>"
        if short_exits_today:
            html += "<p><strong>Short Exits:</strong></p><ul>"
            for e in short_exits_today:
                color = "#27ae60" if e["Return_Pct"] > 0 else "#e74c3c"
                html += f'<li><strong>{e["Ticker"]}</strong> — {e["Reason"]} '
                html += f'(<span style="color:{color}">{e["Return_Pct"]:+.1f}%</span>)</li>'
            html += "</ul>"
        if short_entries_today:
            html += "<p><strong>Short Entries:</strong></p><ul>"
            for e in short_entries_today:
                html += f'<li><strong>{e["Ticker"]}</strong> @ ${e["Price"]:.2f} '
                html += f'(SS: {e["Short_Score"]}, RS: {e["RS_Score"]})</li>'
            html += "</ul>"
        html += "</div>"

    # Section: Long Portfolio
    if holdings_data:
        html += """
        <div style="margin-bottom: 20px;">
            <h3 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px;">
                Long Portfolio
            </h3>
            <table style="border-collapse: collapse; width: 100%; font-size: 12px;">
                <thead>
                    <tr style="background-color: #2c3e50; color: white;">
                        <th style="padding: 8px; text-align: left;">Ticker</th>
                        <th style="padding: 8px;">Entry Date</th>
                        <th style="padding: 8px;">Entry $</th>
                        <th style="padding: 8px;">Current $</th>
                        <th style="padding: 8px;">P&L %</th>
                        <th style="padding: 8px;">Stage</th>
                        <th style="padding: 8px;">Score</th>
                        <th style="padding: 8px;">Trend</th>
                        <th style="padding: 8px;">Alert</th>
                    </tr>
                </thead>
                <tbody>
        """
        for h in sorted(holdings_data, key=lambda x: x["PnL_Pct"], reverse=True):
            pnl_color = "#27ae60" if h["PnL_Pct"] > 0 else "#e74c3c"
            alert_style = 'color: #e74c3c; font-weight: bold;' if h["Alert"] else ''
            trend_color = "#27ae60" if h["Trend"] == "PASS" else "#e74c3c"
            html += f"""
                <tr style="background-color: {'#f9f9f9' if holdings_data.index(h) % 2 == 0 else '#ffffff'};">
                    <td style="padding: 8px; font-weight: bold;">{h['Ticker']}</td>
                    <td style="padding: 8px; text-align: center; font-size: 11px;">{h['Entry_Date']}</td>
                    <td style="padding: 8px; text-align: center;">${h['Entry_Price']:.2f}</td>
                    <td style="padding: 8px; text-align: center;">${h['Current_Price']:.2f}</td>
                    <td style="padding: 8px; text-align: center; color: {pnl_color}; font-weight: bold;">
                        {h['PnL_Pct']:+.1f}%</td>
                    <td style="padding: 8px; text-align: center;">{h['Stage']}</td>
                    <td style="padding: 8px; text-align: center;">{h['Score']}</td>
                    <td style="padding: 8px; text-align: center; color: {trend_color};">{h['Trend']}</td>
                    <td style="padding: 8px; {alert_style}">{h['Alert']}</td>
                </tr>
            """
        html += "</tbody></table></div>"

    # Section: Short Portfolio
    if short_holdings_data:
        html += """
        <div style="margin-bottom: 20px;">
            <h3 style="color: #2c3e50; border-bottom: 2px solid #d32f2f; padding-bottom: 10px;">
                Short Portfolio (NDX100)
            </h3>
            <table style="border-collapse: collapse; width: 100%; font-size: 12px;">
                <thead>
                    <tr style="background-color: #b71c1c; color: white;">
                        <th style="padding: 8px; text-align: left;">Ticker</th>
                        <th style="padding: 8px;">Entry Date</th>
                        <th style="padding: 8px;">Entry $</th>
                        <th style="padding: 8px;">Current $</th>
                        <th style="padding: 8px;">P&L %</th>
                        <th style="padding: 8px;">Stage</th>
                        <th style="padding: 8px;">Score</th>
                        <th style="padding: 8px;">Trend</th>
                        <th style="padding: 8px;">Alert</th>
                    </tr>
                </thead>
                <tbody>
        """
        for h in sorted(short_holdings_data, key=lambda x: x["PnL_Pct"], reverse=True):
            pnl_color = "#27ae60" if h["PnL_Pct"] > 0 else "#e74c3c"
            alert_style = 'color: #e74c3c; font-weight: bold;' if h["Alert"] else ''
            html += f"""
                <tr style="background-color: {'#fff5f5' if short_holdings_data.index(h) % 2 == 0 else '#ffffff'};">
                    <td style="padding: 8px; font-weight: bold;">{h['Ticker']}</td>
                    <td style="padding: 8px; text-align: center; font-size: 11px;">{h['Entry_Date']}</td>
                    <td style="padding: 8px; text-align: center;">${h['Entry_Price']:.2f}</td>
                    <td style="padding: 8px; text-align: center;">${h['Current_Price']:.2f}</td>
                    <td style="padding: 8px; text-align: center; color: {pnl_color}; font-weight: bold;">
                        {h['PnL_Pct']:+.1f}%</td>
                    <td style="padding: 8px; text-align: center;">{h['Stage']}</td>
                    <td style="padding: 8px; text-align: center;">{h['Score']}</td>
                    <td style="padding: 8px; text-align: center;">{h['Trend']}</td>
                    <td style="padding: 8px; {alert_style}">{h['Alert']}</td>
                </tr>
            """
        html += "</tbody></table></div>"

    # Section: Long Waiting List (top 5)
    remaining = [c for c in candidates if c["Ticker"] not in
                 {h["Ticker"] for h in holdings_data}]
    if remaining:
        html += """
        <div style="margin-bottom: 20px;">
            <h3 style="color: #2c3e50; border-bottom: 2px solid #f39c12; padding-bottom: 10px;">
                Long Waiting List (Top 5)
            </h3>
            <table style="border-collapse: collapse; width: 100%; font-size: 12px;">
                <thead>
                    <tr style="background-color: #f39c12; color: white;">
                        <th style="padding: 8px; text-align: left;">Ticker</th>
                        <th style="padding: 8px;">Price</th>
                        <th style="padding: 8px;">Score</th>
                        <th style="padding: 8px;">RS Score</th>
                        <th style="padding: 8px;">Sector</th>
                    </tr>
                </thead>
                <tbody>
        """
        for c in remaining[:5]:
            html += f"""
                <tr>
                    <td style="padding: 8px; font-weight: bold;">{c['Ticker']}</td>
                    <td style="padding: 8px; text-align: center;">${c['Price']:.2f}</td>
                    <td style="padding: 8px; text-align: center;">{c['Score']}</td>
                    <td style="padding: 8px; text-align: center;">{c['RS_Score']}</td>
                    <td style="padding: 8px;">{c['Sector']}</td>
                </tr>
            """
        html += "</tbody></table></div>"

    # Section: Stock Charts (2 long + 2 short, inline images)
    if chart_paths:
        html += """
        <div style="margin-bottom: 20px;">
            <h3 style="color: #2c3e50; border-bottom: 2px solid #8e44ad; padding-bottom: 10px;">
                Stock Charts
            </h3>
        """
        for i, chart_path in enumerate(chart_paths):
            fname = os.path.basename(chart_path)
            # Parse label from filename: chart_long_AAPL.png -> AAPL (LONG)
            parts = fname.replace("chart_", "").replace(".png", "").split("_", 1)
            side = parts[0].upper() if len(parts) > 0 else ""
            ticker = parts[1] if len(parts) > 1 else ""
            cid = f"stock_chart_{i}"
            html += f"""
            <div style="margin-bottom: 15px;">
                <img src="cid:{cid}" style="width: 100%; max-width: 800px; border-radius: 6px;
                     box-shadow: 0 2px 8px rgba(0,0,0,0.1);" />
            </div>
            """
        html += "</div>"

    # Section: Backtest Equity Curves (legacy inline images)
    backtest_charts = []
    chart_dir = os.path.join(PROJECT_ROOT, ".tmp")
    if os.path.isdir(chart_dir):
        for fname in sorted(os.listdir(chart_dir)):
            if fname.startswith("equity_curve_") and fname.endswith(".png"):
                backtest_charts.append(os.path.join(chart_dir, fname))

    if backtest_charts:
        html += """
        <div style="margin-bottom: 20px;">
            <h3 style="color: #2c3e50; border-bottom: 2px solid #8e44ad; padding-bottom: 10px;">
                Backtest Performance
            </h3>
        """
        for i, bk_chart_path in enumerate(backtest_charts):
            label = os.path.basename(bk_chart_path).replace("equity_curve_", "").replace(".png", "").replace("_", " ").upper()
            cid = f"backtest_chart_{i}"
            html += f"""
            <div style="margin-bottom: 15px;">
                <p style="font-size: 12px; color: #555; margin: 5px 0;">{label}</p>
                <img src="cid:{cid}" style="width: 100%; max-width: 800px; border-radius: 6px;
                     box-shadow: 0 2px 8px rgba(0,0,0,0.1);" />
            </div>
            """
        html += "</div>"

    # Footer
    html += """
    <div style="background-color: #ecf0f1; padding: 15px; border-radius: 8px;
                font-size: 11px; color: #7f8c8d;">
        <p style="margin: 0;"><strong>Long Algorithm:</strong>
            Entry: Stage 2 + Score &le; 4 + Trend PASS (ranked by RS) |
            Monthly Exit: Stage &ne; 2 |
            Emergency: -15% or Stage 4
        </p>
        <p style="margin: 5px 0 0 0;"><strong>Short Algorithm (NDX100):</strong>
            Entry: Stage 4 + Short Score &le; 7 (bear flag) |
            Exit: Stage reversal (Stage 2/1) or 40-day time stop
        </p>
        <p style="margin: 5px 0 0 0; font-style: italic;">
            This is not financial advice.
        </p>
    </div>
    </body></html>
    """

    # Send
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.image import MIMEImage

    try:
        n_exits = len(exits_today) + len(short_exits_today)
        n_entries = len(entries_today) + len(short_entries_today)
        action_tag = ""
        if n_exits > 0 or n_entries > 0:
            parts = []
            if n_exits: parts.append(f"{n_exits} exit{'s' if n_exits > 1 else ''}")
            if n_entries: parts.append(f"{n_entries} entr{'ies' if n_entries > 1 else 'y'}")
            action_tag = f" | {', '.join(parts)}"

        subject = (f"Portfolio Tracker — {today} | "
                   f"L:{total_long}/{MAX_SLOTS} S:{total_short}/{MAX_SHORT_SLOTS}"
                   f"{action_tag}")

        msg = MIMEMultipart("related")
        msg["Subject"] = subject
        msg["From"] = gmail_user
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html, "html"))

        # Attach stock charts as inline images
        for i, cp in enumerate(chart_paths):
            try:
                with open(cp, "rb") as img_file:
                    img = MIMEImage(img_file.read(), _subtype="png")
                    img.add_header("Content-ID", f"<stock_chart_{i}>")
                    img.add_header("Content-Disposition", "inline",
                                   filename=os.path.basename(cp))
                    msg.attach(img)
                    logger.info(f"Attached chart: {os.path.basename(cp)}")
            except Exception as e:
                logger.warning(f"Could not attach chart {cp}: {e}")

        # Attach backtest charts as inline images
        for i, bk_chart_path in enumerate(backtest_charts):
            try:
                with open(bk_chart_path, "rb") as img_file:
                    img = MIMEImage(img_file.read(), _subtype="png")
                    img.add_header("Content-ID", f"<backtest_chart_{i}>")
                    img.add_header("Content-Disposition", "inline",
                                   filename=os.path.basename(bk_chart_path))
                    msg.attach(img)
                    logger.info(f"Attached chart: {os.path.basename(bk_chart_path)}")
            except Exception as e:
                logger.warning(f"Could not attach chart {bk_chart_path}: {e}")

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, recipients, msg.as_string())

        logger.info(f"Email sent to {len(recipients)} recipient(s)")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily Portfolio Tracker")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without saving state or sending notifications")
    parser.add_argument("--force-rebalance", action="store_true",
                        help="Force monthly rebalance check regardless of date")
    args = parser.parse_args()

    run_tracker(dry_run=args.dry_run, force_rebalance=args.force_rebalance)

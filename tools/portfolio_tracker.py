"""
Daily Portfolio Tracker — Hybrid System.

Algorithm:
  - Entry: Stage 2 + Score ≤ 4 + Trend Template PASS, ranked by RS Score, max 15 slots
  - Monthly Exit: Last trading day of month, exit if Stage ≠ Stage 2
  - Daily Emergency: Exit if P&L ≤ -15% from entry OR Stage == Stage 4

Outputs:
  - Google Sheets (3 tabs: Portfolio, Candidates, Trade Log)
  - Daily email summary

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

import pandas as pd
import yfinance as yf

# Add project root to path for src imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.screener import analyze_ticker, get_sp500_tickers

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
                logger.info(f"State loaded from Google Sheets (holdings: {len(state.get('holdings', {}))})")
                return state
    except Exception as e:
        logger.warning(f"Could not load state from Sheets: {e}")

    # Fallback to local file
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading local state: {e}")
    return {"last_run": None, "holdings": {}, "trade_log": [],
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
    """Get the ticker universe (top 100 S&P 500 by market cap)."""
    if os.path.exists(TOP100_FILE):
        with open(TOP100_FILE, "r") as f:
            tickers = [line.strip() for line in f if line.strip()]
        logger.info(f"Loaded {len(tickers)} tickers from {TOP100_FILE}")
        return tickers
    # Fallback to full S&P 500
    logger.info("Top 100 file not found, fetching full S&P 500...")
    return get_sp500_tickers()


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
    active_slots = 0
    current_prices = {}

    for h in holdings_data:
        ticker = h["Ticker"]
        current_price = h.get("Current_Price", 0)
        if current_price <= 0:
            continue
        current_prices[ticker] = current_price
        active_slots += 1

        if ticker in prev_prices and prev_prices[ticker] > 0:
            # Daily return for this slot
            slot_ret = (current_price - prev_prices[ticker]) / prev_prices[ticker]
        else:
            # New position — use entry price as "previous"
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

    # Find the closest equity value to Jan 1 of current year
    for entry in history:
        entry_date = entry["date"]
        if entry_date.startswith(str(current_year)):
            jan1_equity = entry["equity"]
            break

    if jan1_equity is None or jan1_equity <= 0:
        # If no history from this year, use first available
        jan1_equity = history[0]["equity"]

    current_equity = state.get("equity", 100.0)
    ytd_pct = (current_equity / jan1_equity - 1) * 100
    return round(ytd_pct, 2)


# ── Core Logic ───────────────────────────────────────────────────────────────

def run_tracker(dry_run: bool = False, force_rebalance: bool = False):
    """Main tracker execution."""
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"=== Portfolio Tracker — {today} ===")

    state = load_state()
    holdings = state["holdings"]
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

    # ── Step 2: Analyze all holdings ─────────────────────────────────────
    exits_today = []
    holdings_data = []

    if holdings:
        logger.info(f"Analyzing {len(holdings)} current holdings...")
        for ticker, pos in list(holdings.items()):
            result = analyze_ticker(ticker, benchmark_df)
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

            holding_row = {
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
            }
            holdings_data.append(holding_row)

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

    # ── Step 3: Scan for candidates ──────────────────────────────────────
    open_slots = MAX_SLOTS - len(holdings)
    logger.info(f"Open slots: {open_slots}/{MAX_SLOTS}")

    universe = get_universe()
    candidates = []
    entries_today = []

    logger.info(f"Scanning {len(universe)} tickers for candidates...")
    for i, ticker in enumerate(universe):
        if (i + 1) % 25 == 0:
            logger.info(f"  Progress: {i + 1}/{len(universe)}")

        if ticker in holdings:
            continue

        result = analyze_ticker(ticker, benchmark_df)
        if result is None:
            continue

        # Entry criteria: Stage 2 + Score ≤ 4 + Trend PASS
        if (result["Stage"] == "Stage 2"
                and result["Score"] <= 4
                and result["Trend_Template"] == "PASS"):
            candidates.append(result)

    # Rank by RS Score descending
    candidates.sort(key=lambda x: x["RS_Score"], reverse=True)
    logger.info(f"Found {len(candidates)} candidates meeting entry criteria")

    # ── Step 4: Fill open slots ──────────────────────────────────────────
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

    # ── Step 5: Update equity ──────────────────────────────────────────────
    # Filter to only active holdings (not exited today)
    active_holdings_data = [h for h in holdings_data if h["Ticker"] in holdings]
    current_equity = update_equity(state, active_holdings_data)
    ytd_pct = calculate_ytd(state)

    # Calculate portfolio average P&L
    active_pnls = [h["PnL_Pct"] for h in active_holdings_data if h.get("Current_Price", 0) > 0]
    avg_pnl = sum(active_pnls) / len(active_pnls) if active_pnls else 0

    # ── Step 6: Update state ─────────────────────────────────────────────
    state["last_run"] = today
    state["holdings"] = holdings
    state["trade_log"] = trade_log

    # ── Print Summary ────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  PORTFOLIO TRACKER — {today}")
    print(f"{'='*60}")
    print(f"  Holdings: {len(holdings)}/{MAX_SLOTS}")
    print(f"  Equity: {current_equity:.2f} (base 100)")
    print(f"  YTD: {ytd_pct:+.2f}%")
    print(f"  Avg P&L (open): {avg_pnl:+.1f}%")
    print(f"  Rebalance Day: {'YES' if rebalance_day else 'No'}")
    print(f"  Exits Today: {len(exits_today)}")
    print(f"  Entries Today: {len(entries_today)}")
    print(f"  Candidates Waiting: {len(candidates) - len(entries_today)}")

    if exits_today:
        print(f"\n  --- Exits ---")
        for e in exits_today:
            print(f"    {e['Ticker']:6s}  {e['Return_Pct']:+6.1f}%  ({e['Reason']})")

    if entries_today:
        print(f"\n  --- New Entries ---")
        for e in entries_today:
            print(f"    {e['Ticker']:6s}  ${e['Price']:>8.2f}  RS: {e['RS_Score']:>6.2f}  Score: {e['Score']}")

    if holdings_data:
        print(f"\n  --- Current Portfolio ---")
        # Sort by P&L descending
        for h in sorted(holdings_data, key=lambda x: x["PnL_Pct"], reverse=True):
            alert = f"  ⚠ {h['Alert']}" if h["Alert"] else ""
            print(f"    {h['Ticker']:6s}  {h['PnL_Pct']:+6.1f}%  {h['Stage']:18s}  Score: {h['Score']:>2}{alert}")

    if candidates:
        remaining = [c for c in candidates if c["Ticker"] not in holdings]
        if remaining:
            print(f"\n  --- Top 5 Waiting List ---")
            for c in remaining[:5]:
                print(f"    {c['Ticker']:6s}  ${c['Price']:>8.2f}  RS: {c['RS_Score']:>6.2f}  Score: {c['Score']}  {c['Sector']}")

    print(f"{'='*60}\n")

    if dry_run:
        logger.info("DRY RUN — No state saved, no email/sheets sent")
        return

    # ── Step 7: Save state ───────────────────────────────────────────────
    save_state(state)

    # ── Step 8: Export to Google Sheets ───────────────────────────────────
    export_google_sheets(holdings_data, candidates, trade_log,
                         state.get("equity_history", []), current_equity, ytd_pct)

    # ── Step 9: Send daily email ─────────────────────────────────────────
    send_tracker_email(
        holdings_data, exits_today, entries_today,
        candidates, rebalance_day, current_equity, ytd_pct
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
                         equity_history=None, current_equity=100, ytd_pct=0):
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

    # Tab 1: Portfolio
    if holdings_data:
        df_portfolio = pd.DataFrame(holdings_data)
        col_order = ["Ticker", "Entry_Date", "Entry_Price", "Current_Price",
                      "PnL_Pct", "Stage", "Score", "Trend", "RS_Score", "Alert"]
        df_portfolio = df_portfolio[[c for c in col_order if c in df_portfolio.columns]]
        ws = _get_or_create_worksheet(sh, "Portfolio", rows=len(df_portfolio) + 5)
        _upload_df(ws, df_portfolio)
        logger.info("Exported Portfolio tab to Sheets")

    # Tab 2: Candidates
    if candidates:
        df_cand = pd.DataFrame(candidates)
        cand_cols = ["Ticker", "Price", "Score", "Stage", "Trend_Template",
                     "RS_Score", "Sector", "Risk_%", "Stop_Loss"]
        df_cand = df_cand[[c for c in cand_cols if c in df_cand.columns]]
        ws = _get_or_create_worksheet(sh, "Candidates", rows=len(df_cand) + 5)
        _upload_df(ws, df_cand)
        logger.info("Exported Candidates tab to Sheets")

    # Tab 3: Trade Log
    if trade_log:
        df_log = pd.DataFrame(trade_log)
        log_cols = ["ticker", "entry_date", "exit_date", "entry_price",
                    "exit_price", "return_pct", "exit_reason"]
        df_log = df_log[[c for c in log_cols if c in df_log.columns]]
        df_log.columns = ["Ticker", "Entry_Date", "Exit_Date", "Entry_Price",
                          "Exit_Price", "Return_Pct", "Reason"]
        ws = _get_or_create_worksheet(sh, "Trade_Log", rows=len(df_log) + 5)
        _upload_df(ws, df_log)
        logger.info("Exported Trade Log tab to Sheets")

    # Tab 4: Equity Curve
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
                       rebalance_day, current_equity=100, ytd_pct=0):
    """Send daily summary email."""
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

    today = datetime.now().strftime("%Y-%m-%d")
    total_holdings = len([h for h in holdings_data if h.get("Current_Price", 0) > 0])

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
            {today} | {total_holdings}/{MAX_SLOTS} slots
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

    # Section: Today's Actions
    if exits_today or entries_today:
        html += """
        <div style="background-color: #fff3cd; padding: 15px; border-radius: 8px;
                    margin-bottom: 20px; border-left: 4px solid #ffc107;">
            <h3 style="margin: 0 0 10px 0; color: #856404;">Today's Actions</h3>
        """
        if exits_today:
            html += "<p><strong>Exits:</strong></p><ul>"
            for e in exits_today:
                color = "#27ae60" if e["Return_Pct"] > 0 else "#e74c3c"
                html += f'<li><strong>{e["Ticker"]}</strong> — {e["Reason"]} '
                html += f'(<span style="color:{color}">{e["Return_Pct"]:+.1f}%</span>)</li>'
            html += "</ul>"
        if entries_today:
            html += "<p><strong>New Entries:</strong></p><ul>"
            for e in entries_today:
                html += f'<li><strong>{e["Ticker"]}</strong> @ ${e["Price"]:.2f} '
                html += f'(RS: {e["RS_Score"]}, Score: {e["Score"]})</li>'
            html += "</ul>"
        html += "</div>"

    # Section: Portfolio
    if holdings_data:
        html += """
        <div style="margin-bottom: 20px;">
            <h3 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px;">
                Current Portfolio
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

    # Section: Waiting List (top 5 candidates not in holdings)
    remaining = [c for c in candidates if c["Ticker"] not in
                 {h["Ticker"] for h in holdings_data}]
    if remaining:
        html += """
        <div style="margin-bottom: 20px;">
            <h3 style="color: #2c3e50; border-bottom: 2px solid #f39c12; padding-bottom: 10px;">
                Waiting List (Top 5)
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

    # Section: Backtest Equity Curves (inline images)
    backtest_charts = []
    chart_dir = os.path.join(PROJECT_ROOT, ".tmp")
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
        for i, chart_path in enumerate(backtest_charts):
            label = os.path.basename(chart_path).replace("equity_curve_", "").replace(".png", "").replace("_", " ").upper()
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
        <p style="margin: 0;"><strong>Algorithm:</strong>
            Entry: Stage 2 + Score ≤ 4 + Trend PASS (ranked by RS) |
            Monthly Exit: Stage ≠ 2 |
            Emergency: -15% or Stage 4
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
        n_exits = len(exits_today)
        n_entries = len(entries_today)
        action_tag = ""
        if n_exits > 0 or n_entries > 0:
            parts = []
            if n_exits: parts.append(f"{n_exits} exit{'s' if n_exits > 1 else ''}")
            if n_entries: parts.append(f"{n_entries} entr{'ies' if n_entries > 1 else 'y'}")
            action_tag = f" | {', '.join(parts)}"

        subject = f"Portfolio Tracker — {today} | {total_holdings}/{MAX_SLOTS}{action_tag}"

        msg = MIMEMultipart("related")
        msg["Subject"] = subject
        msg["From"] = gmail_user
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html, "html"))

        # Attach backtest charts as inline images
        for i, chart_path in enumerate(backtest_charts):
            try:
                with open(chart_path, "rb") as img_file:
                    img = MIMEImage(img_file.read(), _subtype="png")
                    img.add_header("Content-ID", f"<backtest_chart_{i}>")
                    img.add_header("Content-Disposition", "inline",
                                   filename=os.path.basename(chart_path))
                    msg.attach(img)
                    logger.info(f"Attached chart: {os.path.basename(chart_path)}")
            except Exception as e:
                logger.warning(f"Could not attach chart {chart_path}: {e}")

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

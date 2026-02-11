# Daily Portfolio Tracker — Workflow

## Objective
Run the hybrid portfolio tracker daily after market close to manage a 15-slot equity portfolio.

## Algorithm
- **Entry**: Stage 2 + Score ≤ 4 + Trend Template PASS, ranked by RS Score
- **Monthly Exit**: Last trading day of month — exit if Stage ≠ Stage 2
- **Daily Emergency**: Exit if P&L ≤ -15% from entry OR Stage 4
- **Max Slots**: 15 (no rotation — positions stay until exit trigger fires)

## How to Run

```bash
# Full run (saves state, sends email, updates Sheets)
python tools/portfolio_tracker.py

# Dry run (prints results only, no side effects)
python tools/portfolio_tracker.py --dry-run

# Force monthly rebalance check on any day
python tools/portfolio_tracker.py --force-rebalance
```

## When to Run
- **Daily** after market close: 4:30 PM ET or later
- Weekends/holidays: Skip (no new data)

## Required Environment Variables (.env)

| Variable | Description |
|----------|-------------|
| `GMAIL_USER` | Gmail address for sending emails |
| `GMAIL_APP_PASSWORD` | Gmail App Password (not regular password) |
| `EMAIL_RECIPIENTS` | Comma-separated recipient emails |
| `GOOGLE_SHEETS_CREDENTIALS` | Base64-encoded Google service account JSON |
| `TRACKER_SHEET_NAME` | Name of the Google Sheet (default: `Portfolio_Tracker`) |

## Outputs

### Google Sheets (3 tabs)
1. **Portfolio** — Current holdings with P&L, stage, score, alerts
2. **Candidates** — Stocks meeting entry criteria, ranked by RS
3. **Trade_Log** — All historical entries and exits

### Daily Email
- Today's actions (entries/exits)
- Full portfolio with P&L and alerts
- Top 5 waiting list candidates

## State File
- Path: `.tmp/portfolio_state.json`
- Contains: current holdings, trade history, last run date
- Persists between runs — do not delete unless you want to reset

## Edge Cases
- **Ticker delisted**: `analyze_ticker()` returns None — position retained, alert shown
- **Market closed**: No new data from yfinance — prices unchanged, no signals fire
- **First run**: Empty portfolio — fills up to 15 slots from candidates
- **No candidates**: Empty slots remain open until next run
- **Universe file missing**: Falls back to full S&P 500 via Wikipedia

## Automation (optional)
Add to crontab for automatic daily runs:
```
30 16 * * 1-5 cd /path/to/StockPicking && python tools/portfolio_tracker.py >> .tmp/tracker.log 2>&1
```

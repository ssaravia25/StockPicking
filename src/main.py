"""
Trading Matrix - Main Entry Point

Flask application for Google Cloud Run deployment.
Provides HTTP endpoints for triggering the stock screener.
"""

import logging
import os
import sys
from datetime import datetime

from flask import Flask, jsonify, request

from .config import load_config
from .email_sender import send_email
from .screener import filter_buy_candidates, get_sp500_tickers, run_screener
from .sheets_exporter import export_to_sheets

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/", methods=["GET"])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "trading-matrix",
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route("/run-screener", methods=["POST"])
def run_screener_endpoint():
    """
    Run the full screening workflow:
    1. Fetch S&P 500 tickers
    2. Run Minervini screening analysis
    3. Export results to Google Sheets
    4. Send email with buy candidates
    """
    start_time = datetime.utcnow()
    logger.info("Starting screener workflow...")

    try:
        # Load configuration
        config = load_config()
        errors = config.validate()
        if errors:
            logger.error(f"Configuration errors: {errors}")
            return jsonify({
                "status": "error",
                "message": "Configuration validation failed",
                "errors": errors,
            }), 400

        # Get tickers
        if config.ticker_source == "custom" and config.custom_tickers:
            tickers = config.custom_tickers
            logger.info(f"Using {len(tickers)} custom tickers")
        else:
            tickers = get_sp500_tickers()
            if not tickers:
                return jsonify({
                    "status": "error",
                    "message": "Failed to fetch S&P 500 tickers",
                }), 500

        # Run screener
        df_all = run_screener(tickers)
        if df_all.empty:
            logger.warning("Screener returned no results")
            return jsonify({
                "status": "warning",
                "message": "No stocks could be analyzed",
            }), 200

        total_analyzed = len(df_all)

        # Filter buy candidates
        df_buy_candidates = filter_buy_candidates(df_all)

        # Export to Google Sheets
        sheets_url = None
        if config.google_sheets_credentials:
            sheets_url = export_to_sheets(
                df=df_all,
                credentials_dict=config.google_sheets_credentials,
                spreadsheet_name=config.spreadsheet_name,
            )

        # Send email
        email_sent = False
        if config.gmail_user and config.email_recipients:
            email_sent = send_email(
                gmail_user=config.gmail_user,
                gmail_app_password=config.gmail_app_password,
                recipients=config.email_recipients,
                df=df_buy_candidates,
                total_analyzed=total_analyzed,
            )

        # Calculate duration
        duration = (datetime.utcnow() - start_time).total_seconds()

        return jsonify({
            "status": "success",
            "timestamp": datetime.utcnow().isoformat(),
            "duration_seconds": round(duration, 2),
            "results": {
                "total_analyzed": total_analyzed,
                "buy_candidates": len(df_buy_candidates),
                "sheets_url": sheets_url,
                "email_sent": email_sent,
            },
        })

    except Exception as e:
        logger.exception("Screener workflow failed")
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


@app.route("/test-email", methods=["POST"])
def test_email_endpoint():
    """Test email configuration by sending a test message."""
    try:
        config = load_config()

        if not config.gmail_user or not config.gmail_app_password:
            return jsonify({
                "status": "error",
                "message": "Gmail credentials not configured",
            }), 400

        if not config.email_recipients:
            return jsonify({
                "status": "error",
                "message": "No email recipients configured",
            }), 400

        # Create a minimal test DataFrame
        import pandas as pd
        test_df = pd.DataFrame([{
            "Ticker": "TEST",
            "Sector": "Test",
            "Price": 100.00,
            "Score": 1,
            "Stage": "Stage 2",
            "Date_Since_Current_Stage": "2025-01-01",
            "Stage_30_Days_Ago": "Stage 2",
            "Abv10": "Y",
            "Abv20": "Y",
            "Abv50": "Y",
            "Abv200": "Y",
            "Stop_Loss": 95.00,
            "Risk_%": 5.0,
            "ATR": 2.50,
        }])

        success = send_email(
            gmail_user=config.gmail_user,
            gmail_app_password=config.gmail_app_password,
            recipients=config.email_recipients,
            df=test_df,
            total_analyzed=1,
        )

        return jsonify({
            "status": "success" if success else "error",
            "message": "Test email sent" if success else "Failed to send test email",
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


def main():
    """Run the Flask application."""
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    logger.info(f"Starting Trading Matrix server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)


if __name__ == "__main__":
    main()

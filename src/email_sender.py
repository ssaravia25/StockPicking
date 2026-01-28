"""Email sender module using Gmail SMTP."""

import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

import pandas as pd

logger = logging.getLogger(__name__)

GMAIL_SMTP_SERVER = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587


def create_html_table(df: pd.DataFrame) -> str:
    """Convert DataFrame to styled HTML table."""
    if df.empty:
        return "<p>No buy candidates found today.</p>"

    # Define score colors
    def get_score_color(score: int) -> str:
        if score <= 2:
            return "#2ecc71"  # Green
        elif score <= 5:
            return "#f1c40f"  # Yellow
        elif score <= 10:
            return "#3498db"  # Blue
        else:
            return "#e74c3c"  # Red

    # Build HTML table
    html = """
    <table style="border-collapse: collapse; width: 100%; font-family: Arial, sans-serif; font-size: 12px;">
        <thead>
            <tr style="background-color: #2c3e50; color: white;">
    """

    # Headers
    for col in df.columns:
        html += f'<th style="padding: 10px; text-align: left; border: 1px solid #ddd;">{col}</th>'
    html += "</tr></thead><tbody>"

    # Rows
    for _, row in df.iterrows():
        score = row.get("Score", 11)
        score_color = get_score_color(score)

        html += '<tr style="background-color: #f9f9f9;">'
        for col in df.columns:
            value = row[col]
            cell_style = "padding: 8px; border: 1px solid #ddd;"

            if col == "Score":
                cell_style += f" background-color: {score_color}; color: white; font-weight: bold; text-align: center;"
            elif col == "Stage":
                if value == "Stage 2":
                    cell_style += " background-color: #27ae60; color: white;"
            elif col in ["Price", "Stop_Loss"]:
                value = f"${value:,.2f}" if isinstance(value, (int, float)) else value
            elif col == "Risk_%":
                value = f"{value}%" if isinstance(value, (int, float)) else value

            html += f'<td style="{cell_style}">{value}</td>'
        html += "</tr>"

    html += "</tbody></table>"
    return html


def create_email_body(df: pd.DataFrame, total_analyzed: int) -> str:
    """Create the full HTML email body."""
    today = datetime.now().strftime("%Y-%m-%d")

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
    </head>
    <body style="font-family: Arial, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: white; padding: 20px; border-radius: 10px; margin-bottom: 20px;">
            <h1 style="margin: 0;">Trading Matrix Report</h1>
            <p style="margin: 5px 0 0 0; opacity: 0.8;">{today} | Minervini & Stage Analysis</p>
        </div>

        <div style="background-color: #f8f9fa; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="margin: 0 0 10px 0; color: #2c3e50;">Summary</h3>
            <p style="margin: 5px 0;"><strong>Total Stocks Analyzed:</strong> {total_analyzed}</p>
            <p style="margin: 5px 0;"><strong>Buy Candidates Found:</strong> {len(df)}</p>
            <p style="margin: 5px 0;"><strong>Criteria:</strong> Stage 2 + Score 1-4</p>
        </div>

        <div style="margin-bottom: 20px;">
            <h3 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px;">
                Buy Candidates (Stage 2, Score 1-4)
            </h3>
            {create_html_table(df)}
        </div>

        <div style="background-color: #ecf0f1; padding: 15px; border-radius: 8px; font-size: 11px; color: #7f8c8d;">
            <p style="margin: 0;"><strong>Score Guide:</strong></p>
            <ul style="margin: 5px 0; padding-left: 20px;">
                <li><span style="color: #2ecc71;">Score 1-2:</span> Strong buy zone (within 3% of SMA10)</li>
                <li><span style="color: #f1c40f;">Score 3-5:</span> Good entry (within 7% of SMA20)</li>
                <li><span style="color: #3498db;">Score 6-10:</span> Extended position</li>
                <li><span style="color: #e74c3c;">Score 11:</span> Not in buy zone</li>
            </ul>
            <p style="margin: 10px 0 0 0; font-style: italic;">
                Stop Loss = Price - (2 x ATR) | This is not financial advice.
            </p>
        </div>
    </body>
    </html>
    """
    return html


def send_email(
    gmail_user: str,
    gmail_app_password: str,
    recipients: List[str],
    df: pd.DataFrame,
    total_analyzed: int,
) -> bool:
    """
    Send trading matrix results via Gmail SMTP.

    Args:
        gmail_user: Gmail email address
        gmail_app_password: Gmail app password (not regular password)
        recipients: List of recipient email addresses
        df: DataFrame with buy candidates (filtered)
        total_analyzed: Total number of stocks that were analyzed

    Returns:
        True if email sent successfully, False otherwise
    """
    try:
        today = datetime.now().strftime("%Y-%m-%d")

        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Trading Matrix Report - {today} | {len(df)} Buy Candidates"
        msg["From"] = gmail_user
        msg["To"] = ", ".join(recipients)

        # Create HTML body
        html_body = create_email_body(df, total_analyzed)
        html_part = MIMEText(html_body, "html")
        msg.attach(html_part)

        # Send via Gmail SMTP
        logger.info(f"Connecting to Gmail SMTP...")
        with smtplib.SMTP(GMAIL_SMTP_SERVER, GMAIL_SMTP_PORT) as server:
            server.starttls()
            server.login(gmail_user, gmail_app_password)
            server.sendmail(gmail_user, recipients, msg.as_string())

        logger.info(f"Email sent successfully to {len(recipients)} recipient(s)")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error(
            "Gmail authentication failed. "
            "Make sure you're using an App Password, not your regular password. "
            "Generate one at: https://myaccount.google.com/apppasswords"
        )
        return False
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False

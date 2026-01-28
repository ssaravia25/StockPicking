"""Configuration management for Trading Matrix."""

import os
import base64
import json
from dataclasses import dataclass
from typing import List


@dataclass
class Config:
    """Application configuration loaded from environment variables."""

    # Gmail settings
    gmail_user: str
    gmail_app_password: str
    email_recipients: List[str]

    # Google Sheets settings
    google_sheets_credentials: dict
    spreadsheet_name: str

    # Screener settings
    ticker_source: str = "sp500"  # "sp500" or "custom"
    custom_tickers: List[str] = None

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        # Gmail
        gmail_user = os.environ.get("GMAIL_USER", "")
        gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "")

        # Email recipients (comma-separated)
        recipients_str = os.environ.get("EMAIL_RECIPIENTS", "")
        email_recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]

        # Google Sheets credentials (base64-encoded JSON)
        creds_b64 = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
        if creds_b64:
            try:
                creds_json = base64.b64decode(creds_b64).decode("utf-8")
                google_sheets_credentials = json.loads(creds_json)
            except Exception:
                google_sheets_credentials = {}
        else:
            google_sheets_credentials = {}

        spreadsheet_name = os.environ.get(
            "SPREADSHEET_NAME",
            "Trading_Matrix_PremiumSS_Momentum26"
        )

        # Custom tickers (optional)
        custom_tickers_str = os.environ.get("CUSTOM_TICKERS", "")
        custom_tickers = [t.strip() for t in custom_tickers_str.split(",") if t.strip()] or None

        ticker_source = "custom" if custom_tickers else "sp500"

        return cls(
            gmail_user=gmail_user,
            gmail_app_password=gmail_app_password,
            email_recipients=email_recipients,
            google_sheets_credentials=google_sheets_credentials,
            spreadsheet_name=spreadsheet_name,
            ticker_source=ticker_source,
            custom_tickers=custom_tickers,
        )

    def validate(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []

        if not self.gmail_user:
            errors.append("GMAIL_USER is required")
        if not self.gmail_app_password:
            errors.append("GMAIL_APP_PASSWORD is required")
        if not self.email_recipients:
            errors.append("EMAIL_RECIPIENTS is required")
        if not self.google_sheets_credentials:
            errors.append("GOOGLE_SHEETS_CREDENTIALS is required")

        return errors


def load_config() -> Config:
    """Load and return configuration."""
    return Config.from_env()

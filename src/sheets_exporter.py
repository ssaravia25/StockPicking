"""Google Sheets exporter module."""

import logging
import string
from typing import Optional

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from gspread_formatting import (
    BooleanCondition,
    BooleanRule,
    CellFormat,
    Color,
    ConditionalFormatRule,
    GridRange,
    TextFormat,
    format_cell_range,
    set_conditional_format_rules,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_column_letter(col_idx: int) -> str:
    """Convert a 0-based column index to an Excel-style column letter."""
    col_letter = ""
    while col_idx >= 0:
        col_letter = string.ascii_uppercase[col_idx % 26] + col_letter
        col_idx = (col_idx // 26) - 1
    return col_letter


def get_gspread_client(credentials_dict: dict) -> gspread.Client:
    """Create authenticated gspread client from service account credentials."""
    creds = Credentials.from_service_account_info(credentials_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def export_to_sheets(
    df: pd.DataFrame,
    credentials_dict: dict,
    spreadsheet_name: str,
    worksheet_name: str = "The_Matrix",
) -> Optional[str]:
    """
    Export DataFrame to Google Sheets with formatting.

    Args:
        df: DataFrame to export
        credentials_dict: Google service account credentials
        spreadsheet_name: Name of the spreadsheet
        worksheet_name: Name of the worksheet

    Returns:
        URL of the spreadsheet, or None if export failed
    """
    try:
        gc = get_gspread_client(credentials_dict)

        # Open or create spreadsheet
        try:
            sh = gc.open(spreadsheet_name)
            logger.info(f"Opened existing spreadsheet: {spreadsheet_name}")
        except gspread.exceptions.SpreadsheetNotFound:
            sh = gc.create(spreadsheet_name)
            logger.info(f"Created new spreadsheet: {spreadsheet_name}")

        # Open or create worksheet
        try:
            ws = sh.add_worksheet(
                title=worksheet_name,
                rows=str(len(df) + 50),
                cols="20",
            )
            logger.info(f"Created worksheet: {worksheet_name}")
        except gspread.exceptions.APIError:
            ws = sh.worksheet(worksheet_name)
            ws.clear()
            logger.info(f"Cleared existing worksheet: {worksheet_name}")

        # Prepare data
        header = [df.columns.values.tolist()]
        data = df.fillna("").values.tolist()
        max_row = len(data) + 1
        last_col_letter = get_column_letter(len(df.columns) - 1)

        # Upload data
        ws.update(values=header + data, range_name=f"A1:{last_col_letter}{max_row}")
        logger.info(f"Uploaded {len(df)} rows to sheet")

        # Apply formatting
        apply_formatting(ws, df, max_row, last_col_letter)

        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{sh.id}"
        logger.info(f"Export complete: {spreadsheet_url}")
        return spreadsheet_url

    except Exception as e:
        logger.error(f"Failed to export to Google Sheets: {e}")
        return None


def apply_formatting(
    ws: gspread.Worksheet,
    df: pd.DataFrame,
    max_row: int,
    last_col_letter: str,
) -> None:
    """Apply premium formatting to the worksheet."""
    try:
        # Header formatting - bold with gray background
        header_format = CellFormat(
            textFormat=TextFormat(bold=True),
            backgroundColor=Color(0.8, 0.8, 0.8),
        )
        format_cell_range(ws, f"A1:{last_col_letter}1", header_format)

        # Score column conditional formatting (Column D)
        score_colors = [
            (1, 2, Color(0.18, 0.8, 0.44)),  # Green - buy zone
            (3, 5, Color(1, 0.77, 0.05)),  # Yellow - hold
            (6, 10, Color(0.2, 0.6, 0.86)),  # Blue - extended
            (11, 11, Color(0.9, 0.3, 0.23)),  # Red - danger
        ]

        rules = []
        score_range = f"D2:D{max_row}"

        for low, high, color in score_colors:
            rules.append(
                ConditionalFormatRule(
                    ranges=[GridRange.from_a1_range(score_range, ws)],
                    booleanRule=BooleanRule(
                        condition=BooleanCondition(
                            "NUMBER_BETWEEN", [str(low), str(high)]
                        ),
                        format=CellFormat(
                            backgroundColor=color,
                            textFormat=TextFormat(
                                bold=True, foregroundColor=Color(1, 1, 1)
                            ),
                        ),
                    ),
                )
            )

        try:
            set_conditional_format_rules(ws, rules)
        except Exception as e:
            logger.warning(f"Could not apply conditional formatting: {e}")

        # Currency formatting for Price, Stop_Loss, ATR columns
        currency_format = CellFormat(
            numberFormat={"type": "CURRENCY", "pattern": "$#,##0.00"}
        )

        # Find column indices
        columns = df.columns.tolist()
        currency_cols = ["Price", "Stop_Loss", "ATR"]

        for col_name in currency_cols:
            if col_name in columns:
                col_idx = columns.index(col_name)
                col_letter = get_column_letter(col_idx)
                format_cell_range(
                    ws, f"{col_letter}2:{col_letter}{max_row}", currency_format
                )

        logger.info("Applied formatting to worksheet")

    except Exception as e:
        logger.warning(f"Error applying formatting: {e}")

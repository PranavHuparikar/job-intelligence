"""
feedback_store.py — Persistent feedback storage via Google Sheets.

On Streamlit Cloud: writes each submission as a new row in a Google Sheet
you own. Credentials are read from st.secrets (never from disk).

On local / no credentials: falls back silently to feedback.jsonl.

Required Streamlit secrets (set in Streamlit Cloud → App settings → Secrets):

    FEEDBACK_SHEET_ID = "your-google-sheet-id-here"

    [gcp_service_account]
    type = "service_account"
    project_id = "your-project-id"
    private_key_id = "..."
    private_key = "-----BEGIN RSA PRIVATE KEY-----\\n...\\n-----END RSA PRIVATE KEY-----\\n"
    client_email = "your-sa@your-project.iam.gserviceaccount.com"
    client_id = "..."
    auth_uri = "https://accounts.google.com/o/oauth2/auth"
    token_uri = "https://oauth2.googleapis.com/token"
    auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
    client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/..."

Sheet columns (auto-created on first write if sheet is empty):
    Timestamp | Stars | Comment | Company | Model | Role Detected
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Column headers written to row 1 if the sheet is blank ────────────────────
_HEADERS = ["Timestamp", "Stars", "Comment", "Company", "Model"]


def _get_sheet():
    """
    Return the first worksheet of the configured Google Sheet.
    Raises if credentials or sheet ID are not configured.
    """
    import streamlit as st
    from google.oauth2.service_account import Credentials
    import gspread

    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    sheet_id = st.secrets["FEEDBACK_SHEET_ID"]
    return gc.open_by_key(sheet_id).sheet1


def _ensure_headers(sheet) -> None:
    """Write column headers to row 1 if the sheet is completely empty."""
    try:
        if not sheet.row_values(1):
            sheet.append_row(_HEADERS, value_input_option="RAW")
    except Exception:
        pass


def save_feedback(
    stars:   int,
    comment: str,
    company: str = "",
    model:   str = "",
) -> tuple[bool, str]:
    """
    Save one feedback entry.

    Returns (success: bool, message: str).
    Tries Google Sheets first; falls back to local feedback.jsonl.
    """
    ts = datetime.now().isoformat(timespec="seconds")

    # ── Try Google Sheets ─────────────────────────────────────────────────────
    try:
        import streamlit as st
        if "gcp_service_account" in st.secrets and "FEEDBACK_SHEET_ID" in st.secrets:
            sheet = _get_sheet()
            _ensure_headers(sheet)
            sheet.append_row(
                [ts, stars, comment.strip(), company, model],
                value_input_option="RAW",
            )
            return True, "sheets"
    except Exception:
        pass  # fall through to local file

    # ── Fallback: local JSONL ─────────────────────────────────────────────────
    try:
        entry = {
            "ts":      ts,
            "stars":   stars,
            "comment": comment.strip(),
            "company": company,
            "model":   model,
        }
        with open(Path(__file__).parent / "feedback.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return True, "local"
    except Exception as e:
        return False, str(e)


def is_sheets_configured() -> bool:
    """Return True if Google Sheets credentials are present in st.secrets."""
    try:
        import streamlit as st
        return (
            "gcp_service_account" in st.secrets
            and "FEEDBACK_SHEET_ID" in st.secrets
        )
    except Exception:
        return False

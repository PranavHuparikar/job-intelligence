"""
razorpay_handler.py — Razorpay Payment Links integration.

Flow
----
1. User picks a bundle in the sidebar → create_payment_link(email, bundle_key)
   returns a short hosted checkout URL → open in new tab.
2. After payment Razorpay redirects back to APP_URL with signed query params.
3. On the next page load streamlit_app.py calls handle_payment_callback()
   which verifies the signature and credits the user.

Secrets required (add to .streamlit/secrets.toml or .env)
----------------------------------------------------------
RAZORPAY_KEY_ID      rzp_live_...   (or rzp_test_... for testing)
RAZORPAY_KEY_SECRET  <your secret>
APP_URL              https://your-app.streamlit.app  (callback redirect base URL)

Run setup_supabase_credits.sql in Supabase before using (creates user_credits,
payment_log, and the deduct_credit RPC).
"""

from __future__ import annotations

import os
from typing import Optional

from credits import BUNDLES, add_credits


# ── Secret reader ─────────────────────────────────────────────────────────────

def _get_secret(key: str) -> str:
    try:
        import streamlit as st
        val = st.secrets.get(key, "")
        if val:
            return str(val)
    except Exception:
        pass
    return os.getenv(key, "")


def razorpay_configured() -> bool:
    """Return True when the Razorpay key pair is present."""
    return bool(_get_secret("RAZORPAY_KEY_ID")) and bool(
        _get_secret("RAZORPAY_KEY_SECRET")
    )


# ── Razorpay client (lazy) ────────────────────────────────────────────────────

def _get_client():
    try:
        import razorpay
    except ImportError:
        raise ImportError(
            "razorpay package not installed. Run: pip install razorpay"
        )
    return razorpay.Client(
        auth=(_get_secret("RAZORPAY_KEY_ID"), _get_secret("RAZORPAY_KEY_SECRET"))
    )


# ── Public functions ──────────────────────────────────────────────────────────

def create_payment_link(email: str, bundle_key: str) -> str:
    """
    Create a Razorpay Payment Link for *email* buying *bundle_key*.

    Returns the short_url the user should open in their browser.
    Raises KeyError if *bundle_key* is not in BUNDLES.
    Raises RuntimeError on Razorpay API errors.
    """
    bundle = BUNDLES[bundle_key]          # KeyError → caller's problem
    client = _get_client()

    app_url  = _get_secret("APP_URL").rstrip("/")
    callback = f"{app_url}?rzp_bundle={bundle_key}"   # carry bundle key in redirect

    amount_paise = bundle["price_inr"] * 100          # Razorpay uses paise

    payload = {
        "amount":       amount_paise,
        "currency":     "INR",
        "description":  f"Job Intelligence — {bundle['label']}",
        "customer": {
            "email": email,
        },
        "notify": {
            "email": True,
        },
        "reminder_enable": False,
        "callback_url":    callback,
        "callback_method": "get",
        "notes": {
            "email":      email,
            "bundle_key": bundle_key,
            "runs":       str(bundle["runs"]),
        },
    }

    try:
        link = client.payment_link.create(payload)
        return link["short_url"]
    except Exception as exc:
        raise RuntimeError(f"Razorpay create_payment_link failed: {exc}") from exc


def handle_payment_callback(query_params: dict) -> dict:
    """
    Verify a Razorpay Payment Link callback and credit the user.

    *query_params* should be ``dict(st.query_params)`` — Streamlit passes the
    URL query string as a mapping of str → str (single values, not lists).

    Returns a result dict:
        {"success": True,  "email": ..., "runs": ..., "balance": ...}
        {"success": False, "error": "...reason..."}

    Side-effects on success: calls add_credits() which is idempotent.
    """
    required = {
        "razorpay_payment_link_id",
        "razorpay_payment_link_reference_id",
        "razorpay_payment_link_status",
        "razorpay_payment_id",
        "razorpay_signature",
    }
    missing = required - set(query_params)
    if missing:
        return {"success": False, "error": f"Missing callback params: {missing}"}

    status = query_params.get("razorpay_payment_link_status", "")
    if status != "paid":
        return {"success": False, "error": f"Payment status is '{status}' — not paid."}

    # ── Signature verification ─────────────────────────────────────────────────
    try:
        client = _get_client()
        client.utility.verify_payment_link_signature(
            {
                "payment_link_id":           query_params["razorpay_payment_link_id"],
                "payment_link_reference_id": query_params["razorpay_payment_link_reference_id"],
                "payment_link_status":       status,
                "razorpay_payment_id":       query_params["razorpay_payment_id"],
                "razorpay_signature":        query_params["razorpay_signature"],
            }
        )
    except Exception as exc:
        return {"success": False, "error": f"Signature verification failed: {exc}"}

    # ── Fetch payment details to get email + bundle ────────────────────────────
    payment_id = query_params["razorpay_payment_id"]
    link_id    = query_params["razorpay_payment_link_id"]
    bundle_key = query_params.get("rzp_bundle", "")   # passed via callback_url

    try:
        client   = _get_client()
        link_obj = client.payment_link.fetch(link_id)
        email    = (
            link_obj.get("notes", {}).get("email")
            or link_obj.get("customer", {}).get("email", "")
        )
        if not bundle_key:
            bundle_key = link_obj.get("notes", {}).get("bundle_key", "")
    except Exception as exc:
        return {"success": False, "error": f"Could not fetch payment link: {exc}"}

    if not email:
        return {"success": False, "error": "Could not determine email from payment."}

    bundle = BUNDLES.get(bundle_key)
    if not bundle:
        return {"success": False, "error": f"Unknown bundle key: {bundle_key!r}"}

    # ── Credit the user (idempotent) ───────────────────────────────────────────
    try:
        new_balance = add_credits(
            email        = email,
            payment_id   = payment_id,
            runs_added   = bundle["runs"],
            bundle_label = bundle["label"],
            amount_inr   = bundle["price_inr"],
        )
    except Exception as exc:
        return {"success": False, "error": f"add_credits failed: {exc}"}

    return {
        "success": True,
        "email":   email,
        "runs":    bundle["runs"],
        "balance": new_balance,
        "bundle":  bundle["label"],
    }

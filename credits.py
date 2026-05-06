"""
credits.py — Supabase-backed credit ledger for the Job Intelligence System.

Each user has a credits balance stored in the `user_credits` table (keyed by
email). Credits are purchased in bundles and deducted atomically via the
`deduct_credit()` Postgres RPC (defined in setup_supabase_credits.sql).

Public API
----------
BUNDLES           dict — bundle config: {key: {label, runs, price_inr, price_display}}
get_credits(email) → int
add_credits(email, payment_id, runs_added, bundle_label, amount_inr) → int  (idempotent)
deduct_credit(email) → int   (raises InsufficientCreditsError if balance is 0)
credits_configured() → bool  (False when Supabase env vars are missing)

Requires: SUPABASE_URL, SUPABASE_KEY in .env / Streamlit secrets.
Run setup_supabase_credits.sql once before using.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

# ── Bundle catalogue ──────────────────────────────────────────────────────────
#
# Each bundle key maps to a display label, the number of runs it grants,
# and the price in INR (paise are NOT used here — the Razorpay handler converts).

BUNDLES: dict[str, dict] = {
    "starter": {
        "label":         "Starter — 5 runs",
        "runs":          5,
        "price_inr":     199,
        "price_display": "₹199",
    },
    "standard": {
        "label":         "Standard — 10 runs",
        "runs":          10,
        "price_inr":     349,
        "price_display": "₹349",
    },
    "pro": {
        "label":         "Pro — 25 runs",
        "runs":          25,
        "price_inr":     749,
        "price_display": "₹749",
    },
}


# ── Custom exception ──────────────────────────────────────────────────────────

class InsufficientCreditsError(Exception):
    """Raised by deduct_credit() when the user has 0 credits."""


# ── Secret reader (mirrors auth.py / jd_database.py pattern) ─────────────────

def _get_secret(key: str) -> str:
    try:
        import streamlit as st
        val = st.secrets.get(key, "")
        if val:
            return str(val)
    except Exception:
        pass
    return os.getenv(key, "")


def credits_configured() -> bool:
    """Return True when SUPABASE_URL + SUPABASE_KEY are both present."""
    return bool(_get_secret("SUPABASE_URL")) and bool(_get_secret("SUPABASE_KEY"))


# ── Supabase client (lazy singleton, thread-safe) ─────────────────────────────

_sb_client = None
_sb_lock   = threading.Lock()


def _get_supabase():
    global _sb_client
    if _sb_client is None:
        with _sb_lock:
            if _sb_client is None:
                try:
                    from supabase import create_client
                except ImportError:
                    raise ImportError(
                        "supabase package not installed. Run: pip install supabase"
                    )
                url = _get_secret("SUPABASE_URL")
                key = _get_secret("SUPABASE_KEY")
                if not url or not key:
                    raise EnvironmentError(
                        "SUPABASE_URL and SUPABASE_KEY must be set to use credits."
                    )
                _sb_client = create_client(url, key)
    return _sb_client


# ── Public functions ──────────────────────────────────────────────────────────

def get_credits(email: str) -> int:
    """
    Return the current credit balance for *email*.
    Returns 0 if the user has no row yet or Supabase is unavailable.
    """
    email = email.lower().strip()
    try:
        sb  = _get_supabase()
        res = (
            sb.table("user_credits")
            .select("credits")
            .eq("email", email)
            .maybe_single()
            .execute()
        )
        if res.data:
            return int(res.data.get("credits", 0))
        return 0
    except Exception as exc:
        print(f"[credits] get_credits({email!r}) failed: {exc}")
        return 0


def add_credits(
    email:        str,
    payment_id:   str,
    runs_added:   int,
    bundle_label: str,
    amount_inr:   int,
) -> int:
    """
    Credit *runs_added* runs to *email* after a successful payment.

    Idempotent — if *payment_id* already exists in payment_log this is a
    no-op and the current balance is returned unchanged.

    Returns the new credit balance (or current balance on duplicate).
    """
    email      = email.lower().strip()
    payment_id = payment_id.strip()

    sb = _get_supabase()

    # ── 1. Idempotency check — has this payment already been credited? ─────────
    try:
        dup = (
            sb.table("payment_log")
            .select("payment_id")
            .eq("payment_id", payment_id)
            .maybe_single()
            .execute()
        )
        if dup.data:
            print(f"[credits] payment {payment_id!r} already credited — skipping.")
            return get_credits(email)
    except Exception as exc:
        print(f"[credits] idempotency check failed: {exc}")
        # Fall through and try to credit anyway (worst case: manual review)

    # ── 2. Upsert user_credits row ────────────────────────────────────────────
    try:
        sb.table("user_credits").upsert(
            {
                "email":          email,
                "credits":        runs_added,   # will be added via RPC below
                "total_purchased": runs_added,
                "total_used":     0,
            },
            on_conflict="email",
            ignore_duplicates=False,
        ).execute()
    except Exception:
        pass  # row may already exist; the UPDATE below handles the balance

    # Use a raw increment so concurrent calls don't overwrite each other
    try:
        sb.rpc(
            "add_credits",
            {"user_email": email, "amount": runs_added},
        ).execute()
    except Exception:
        # Fallback: read current value then write back (non-atomic but acceptable
        # since payment_log idempotency prevents double-crediting)
        current = get_credits(email)
        sb.table("user_credits").upsert(
            {
                "email":           email,
                "credits":         current + runs_added,
                "total_purchased": current + runs_added,  # approximate
                "total_used":      0,
            },
            on_conflict="email",
        ).execute()

    # ── 3. Log the payment ────────────────────────────────────────────────────
    try:
        sb.table("payment_log").insert(
            {
                "payment_id":   payment_id,
                "email":        email,
                "runs_added":   runs_added,
                "bundle_label": bundle_label,
                "amount_inr":   amount_inr,
            }
        ).execute()
    except Exception as exc:
        print(f"[credits] payment_log insert failed: {exc}")

    new_balance = get_credits(email)
    print(f"[credits] +{runs_added} credits → {email!r}  (balance: {new_balance})")
    return new_balance


def deduct_credit(email: str) -> int:
    """
    Atomically deduct 1 credit from *email* via the deduct_credit() Postgres RPC.

    Returns the new balance.
    Raises InsufficientCreditsError if the balance is 0.
    Raises RuntimeError on unexpected Supabase errors.
    """
    email = email.lower().strip()
    try:
        sb  = _get_supabase()
        res = sb.rpc("deduct_credit", {"user_email": email}).execute()
        # The RPC returns the new credits integer
        new_balance = int(res.data) if res.data is not None else 0
        print(f"[credits] -1 credit → {email!r}  (balance: {new_balance})")
        return new_balance
    except Exception as exc:
        msg = str(exc).lower()
        if "insufficient" in msg or "credits" in msg:
            raise InsufficientCreditsError(
                f"No credits remaining for {email}. Please purchase a bundle."
            ) from exc
        raise RuntimeError(f"[credits] deduct_credit failed: {exc}") from exc

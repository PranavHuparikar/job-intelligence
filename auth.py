"""
auth.py — Google OAuth login via Supabase Auth.

PKCE is managed manually to survive Streamlit's WebSocket-reset on OAuth redirect.

Problem with supabase-py v2 default behaviour:
  sign_in_with_oauth() generates a code_verifier and stores it inside the
  Supabase client instance.  When the user's browser leaves the Streamlit page
  (going to Google) the WebSocket is closed, the server-side Python session is
  torn down, and the client instance — plus its code_verifier — is gone.
  When the callback arrives (?code=...) Streamlit boots a fresh session; the
  new Supabase client has no code_verifier, so exchange_code_for_session fails.

Fix:
  Generate the PKCE code_verifier ourselves, build the Supabase /authorize URL
  manually, and park the code_verifier in a module-level dict keyed by the
  `state` param.  Module-level state in Python survives across Streamlit
  requests as long as the server process is running (which is always true on
  Streamlit Cloud with a single worker per app).

Supabase setup (one-time):
  Dashboard → Authentication → Providers → Google → enable → paste
  Client ID + Client Secret from Google Cloud Console.
  Copy the "Callback URL" Supabase shows → paste into Google Cloud Console
  under Authorised redirect URIs.

  Dashboard → Authentication → URL Configuration:
    Site URL            = https://your-app.streamlit.app
    Redirect URLs       = https://your-app.streamlit.app   ← add this

Streamlit secrets required:
  SUPABASE_URL = "https://xxxx.supabase.co"
  SUPABASE_KEY = "eyJ..."          # anon or service-role key
  APP_URL      = "https://your-app.streamlit.app"
"""

from __future__ import annotations  # allows dict | None syntax on Python 3.9

import base64
import hashlib
import os
import secrets
from urllib.parse import quote

import streamlit as st

# ---------------------------------------------------------------------------
# Module-level PKCE store.
# Maps  state_token  →  code_verifier.
# Lives in the server process; survives across Streamlit WebSocket resets.
# ---------------------------------------------------------------------------
_pkce_store: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Secret helpers
# ---------------------------------------------------------------------------

def _secret(key: str, default: str = "") -> str:
    try:
        val = st.secrets.get(key, default)
        return str(val) if val else default
    except Exception:
        return os.getenv(key, default)


def _supabase_configured() -> bool:
    return bool(
        _secret("SUPABASE_URL")
        and (_secret("SUPABASE_KEY") or _secret("SUPABASE_ANON_KEY"))
    )


def _get_supabase():
    from supabase import create_client
    key = _secret("SUPABASE_KEY") or _secret("SUPABASE_ANON_KEY")
    return create_client(_secret("SUPABASE_URL"), key)


# ---------------------------------------------------------------------------
# Manual PKCE helpers
# ---------------------------------------------------------------------------

def _make_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using SHA-256 / S256 method."""
    code_verifier = secrets.token_urlsafe(64)                        # 86-char URL-safe random string
    digest        = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _google_oauth_url() -> str:
    """
    Build the Supabase Google OAuth redirect URL using our own PKCE params.
    Stores the code_verifier server-side in _pkce_store, keyed by state.
    """
    supabase_url = _secret("SUPABASE_URL").rstrip("/")
    app_url      = _secret("APP_URL", "http://localhost:8501")

    code_verifier, code_challenge = _make_pkce_pair()
    state = secrets.token_urlsafe(24)         # random opaque value, also ties callback to verifier

    _pkce_store[state] = code_verifier

    # Evict old entries so the dict doesn't grow unbounded on long-running servers
    if len(_pkce_store) > 500:
        for old_key in list(_pkce_store.keys())[:-250]:
            _pkce_store.pop(old_key, None)

    return (
        f"{supabase_url}/auth/v1/authorize"
        f"?provider=google"
        f"&redirect_to={quote(app_url, safe='')}"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
        f"&state={state}"
    )


# ---------------------------------------------------------------------------
# Code exchange
# ---------------------------------------------------------------------------

def _exchange_code(code: str, state: str = "") -> tuple[dict | None, str]:
    """
    Exchange the OAuth auth_code for a Supabase session.
    Returns (user_dict, None) on success, or (None, error_message) on failure.

    Retrieves code_verifier from the module-level store using `state`.
    If the verifier is missing (e.g. dev restart wiped the store) we still
    attempt the exchange without it — Supabase will reject it, and the user
    just has to sign in again.
    """
    try:
        code_verifier = _pkce_store.pop(state, None) if state else None
        supabase      = _get_supabase()

        params: dict = {"auth_code": code}
        if code_verifier:
            params["code_verifier"] = code_verifier

        resp = supabase.auth.exchange_code_for_session(params)
        user = resp.user
        if not user:
            return None, "No user returned in session response."

        return {
            "email":  user.email or "",
            "name":   (user.user_metadata or {}).get("full_name", ""),
            "avatar": (user.user_metadata or {}).get("avatar_url", ""),
        }, None

    except Exception as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# Sign-out
# ---------------------------------------------------------------------------

def sign_out() -> None:
    """Clear local session and revoke the Supabase session token."""
    st.session_state.pop("_auth_user",    None)
    st.session_state.pop("authenticated", None)
    st.session_state.pop("user_email",    None)
    try:
        if _supabase_configured():
            _get_supabase().auth.sign_out()
    except Exception:
        pass
    st.rerun()


# ---------------------------------------------------------------------------
# Main auth gate
# ---------------------------------------------------------------------------

def check_password() -> bool:
    """
    Render the auth gate and return True when the user is authenticated.

    Google OAuth via Supabase is mandatory — no password / open-access fallback.

    After a successful login, session_state["user_email"] is set automatically
    so the credits system and data-isolation logic pick it up without extra wiring.
    """
    # ── Already authenticated ────────────────────────────────────────────────
    if st.session_state.get("_auth_user"):
        user = st.session_state["_auth_user"]
        if user.get("email") and not st.session_state.get("user_email"):
            st.session_state["user_email"] = user["email"]
        return True

    # ── Handle OAuth callback (?code=...&state=...) ──────────────────────────
    try:
        code  = st.query_params.get("code", "")
        state = st.query_params.get("state", "")
    except Exception:
        code, state = "", ""

    # Ignore Razorpay payment callbacks that also carry a `code` param
    if code and _supabase_configured() and "razorpay_payment_id" not in st.query_params:
        try:
            with st.spinner("Signing you in…"):
                user_dict, err = _exchange_code(code, state)
        except Exception as exc:
            user_dict, err = None, str(exc)

        # Always clear the code from the URL — stale codes cause blank-page loops
        try:
            st.query_params.clear()
        except Exception:
            pass

        if user_dict:
            st.session_state["_auth_user"]    = user_dict
            st.session_state["authenticated"] = True
            st.session_state["user_email"]    = user_dict["email"]
            st.rerun()
        else:
            st.warning(
                f"Sign-in could not complete ({err}). Please try again.",
                icon="🔒",
            )
            # Fall through and render the login button

    # ── Show login UI ────────────────────────────────────────────────────────
    return _google_login_page()


# ---------------------------------------------------------------------------
# Login UI
# ---------------------------------------------------------------------------

def _google_login_page() -> bool:
    _, col, _ = st.columns([1, 1.6, 1])
    with col:
        st.markdown(
            "<h1 style='text-align:center;margin-bottom:4px'>🎯 Job Intelligence</h1>"
            "<p style='text-align:center;color:#888;margin-bottom:36px;font-size:1.05rem'>"
            "AI-powered job analysis &middot; CV tailoring &middot; Company intel</p>",
            unsafe_allow_html=True,
        )

        if not _supabase_configured():
            st.error(
                "Google sign-in is not configured. "
                "Please add **SUPABASE_URL**, **SUPABASE_KEY**, and **APP_URL** "
                "to Streamlit secrets.",
                icon="🔒",
            )
            return False

        try:
            oauth_url = _google_oauth_url()
            st.link_button(
                "  Sign in with Google",
                oauth_url,
                use_container_width=True,
                type="primary",
            )
        except Exception as exc:
            st.error(f"Could not generate sign-in link: {exc}", icon="⚠️")
            return False

        st.markdown(
            "<p style='text-align:center;color:#aaa;font-size:0.82rem;margin-top:16px'>"
            "We only read your email address to track run credits.<br>"
            "We never post on your behalf or access Gmail.</p>",
            unsafe_allow_html=True,
        )

    return False

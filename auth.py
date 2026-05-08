"""
auth.py — Google OAuth login via Supabase Auth.

Why manual PKCE:
  supabase-py v2 stores code_verifier inside the client instance (in-memory).
  When the user's browser leaves Streamlit (going to Google) the WebSocket
  closes, the Python session is torn down, and the code_verifier is gone.
  On callback a fresh client is created with no code_verifier, so
  exchange_code_for_session fails and Supabase falls back to the Site URL.

Fix — two-layer fallback:
  Layer 1: Generate our own PKCE pair, build the /authorize URL manually,
           store code_verifier in module-level dict keyed by `state`.
           Works when Supabase echoes our `state` back in the redirect URL.
  Layer 2: Also keep a "_fallback" slot with the most-recently generated
           verifier.  Used when Supabase strips/rewrites the `state` param.

Supabase dashboard (one-time):
  Authentication → Providers → Google → enable → paste Client ID + Secret.
  Authentication → URL Configuration:
    Site URL     = https://your-app.streamlit.app
    Redirect URLs = https://your-app.streamlit.app  ← exact, no trailing slash

Streamlit secrets required:
  SUPABASE_URL = "https://xxxx.supabase.co"
  SUPABASE_KEY = "eyJ..."
  APP_URL      = "https://your-app.streamlit.app"
"""

from __future__ import annotations  # allows dict|None on Python 3.9+

import base64
import hashlib
import os
import secrets
from urllib.parse import quote, urlparse

import streamlit as st

# ---------------------------------------------------------------------------
# Module-level PKCE store — survives Streamlit WebSocket resets in the same
# server process (Streamlit Cloud runs one process per app).
# ---------------------------------------------------------------------------
_pkce_store: dict[str, str] = {}          # {state: code_verifier}
_pkce_fallback: list[str]   = []          # [latest_code_verifier]  (single slot)

_last_exchange_error: list[str] = []      # debug: last error from exchange


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
# PKCE helpers
# ---------------------------------------------------------------------------

def _make_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) — SHA-256 / S256."""
    verifier   = secrets.token_urlsafe(64)
    digest     = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge  = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _google_oauth_url() -> str:
    """
    Build Supabase /auth/v1/authorize URL with our own PKCE params.
    Stores code_verifier in both the state-keyed dict and the fallback slot.
    """
    supabase_url = _secret("SUPABASE_URL", "").rstrip("/")
    app_url      = _secret("APP_URL", "http://localhost:8501").rstrip("/")

    if not supabase_url:
        raise ValueError("SUPABASE_URL secret is empty — cannot build OAuth URL.")

    verifier, challenge = _make_pkce_pair()
    state = secrets.token_urlsafe(24)

    # Layer 1: state-keyed store
    _pkce_store[state] = verifier
    if len(_pkce_store) > 500:
        for old in list(_pkce_store.keys())[:-250]:
            _pkce_store.pop(old, None)

    # Layer 2: fallback slot (most recent verifier)
    _pkce_fallback.clear()
    _pkce_fallback.append(verifier)

    return (
        f"{supabase_url}/auth/v1/authorize"
        f"?provider=google"
        f"&redirect_to={quote(app_url, safe='')}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
        f"&state={state}"
    )


# ---------------------------------------------------------------------------
# Code exchange
# ---------------------------------------------------------------------------

def _exchange_code(code: str, state: str = "") -> tuple[dict | None, str]:
    """
    Exchange OAuth auth_code for a Supabase session.
    Returns (user_dict, None) on success or (None, error_str) on failure.
    """
    try:
        # Layer 1: state-keyed lookup
        verifier = _pkce_store.pop(state, None) if state else None

        # Layer 2: fallback to most recent verifier if state wasn't forwarded
        if verifier is None and _pkce_fallback:
            verifier = _pkce_fallback.pop()

        supabase = _get_supabase()
        params: dict = {"auth_code": code}
        if verifier:
            params["code_verifier"] = verifier

        resp = supabase.auth.exchange_code_for_session(params)
        user = resp.user
        if not user:
            return None, "No user in session response."

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
    """Return True when authenticated; show login page otherwise."""

    # Already authenticated
    if st.session_state.get("_auth_user"):
        user = st.session_state["_auth_user"]
        if user.get("email") and not st.session_state.get("user_email"):
            st.session_state["user_email"] = user["email"]
        return True

    # Read OAuth callback params
    try:
        code  = st.query_params.get("code",  "")
        state = st.query_params.get("state", "")
    except Exception:
        code, state = "", ""

    # Handle callback (ignore Razorpay redirects that also carry `code`)
    if code and _supabase_configured() and "razorpay_payment_id" not in st.query_params:
        try:
            with st.spinner("Signing you in…"):
                user_dict, err = _exchange_code(code, state)
        except Exception as exc:
            user_dict, err = None, str(exc)

        # Always wipe the code from the URL (prevents reuse on refresh)
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
            # Save error for debug panel
            _last_exchange_error.clear()
            _last_exchange_error.append(err or "unknown error")
            st.warning(f"Sign-in could not complete — please try again.", icon="🔒")

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
                "Add **SUPABASE_URL**, **SUPABASE_KEY**, and **APP_URL** to Streamlit secrets.",
                icon="🔒",
            )
            return False

        try:
            oauth_url = _google_oauth_url()

            # Quick sanity check — URL must point to Supabase, not be relative
            parsed = urlparse(oauth_url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError(f"Malformed OAuth URL: {oauth_url!r}")

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
            "Used only to identify your account and track run credits.<br>"
            "No access to your Gmail, Drive, or any Google data.</p>",
            unsafe_allow_html=True,
        )

        # Debug panel — shows last exchange error and the OAuth endpoint being used
        with st.expander("🔧 Debug info", expanded=False):
            try:
                su       = _secret("SUPABASE_URL", "").rstrip("/")
                app_raw  = _secret("APP_URL", "")
                app_sent = app_raw.rstrip("/")   # what is actually in redirect_to
                st.markdown(f"**Supabase project:** `{urlparse(su).netloc}`")
                st.markdown(f"**redirect\\_to sent to Supabase:** `{app_sent}`")
                st.markdown(
                    "⚠️ Add **both** of these to Supabase → Authentication → "
                    "URL Configuration → Redirect URLs:"
                )
                st.code(f"{app_sent}\n{app_sent}/", language="text")
                st.markdown(f"**Pending PKCE entries:** {len(_pkce_store)}")
                st.markdown(f"**Fallback verifier ready:** {bool(_pkce_fallback)}")
            except Exception:
                pass
            if _last_exchange_error:
                st.error(f"Last exchange error: {_last_exchange_error[-1]}", icon="🔒")
            else:
                st.info("No exchange attempt yet this session.")

    return False

"""
auth.py — Google OAuth login via Supabase Auth.

Flow:
  1. User clicks "Sign in with Google"
  2. Browser redirected to Google -> user picks account
  3. Google redirects to Supabase callback -> Supabase redirects back to app
     with ?code=... in the URL query string
  4. App exchanges code for session -> gets user email + name + avatar
  5. Email stored in session_state; credits system uses it automatically

Supabase setup (one-time):
  Dashboard -> Authentication -> Providers -> Google -> enable -> paste
  Client ID + Client Secret from Google Cloud Console.
  Copy the "Callback URL" Supabase shows -> paste into Google Cloud Console
  under Authorized redirect URIs.

Streamlit secrets required:
  SUPABASE_URL      = "https://xxxx.supabase.co"
  SUPABASE_ANON_KEY = "eyJ..."
  APP_URL           = "https://your-app.streamlit.app"

Optional legacy fallback (remove once OAuth is live):
  APP_PASSWORD = "..."   <- used only when Supabase is NOT configured
"""

import os
import streamlit as st


# -- Secret helpers -----------------------------------------------------------

def _secret(key: str, default: str = "") -> str:
    try:
        val = st.secrets.get(key, default)
        return str(val) if val else default
    except Exception:
        return os.getenv(key, default)


def _supabase_configured() -> bool:
    return bool(_secret("SUPABASE_URL") and _secret("SUPABASE_ANON_KEY"))


def _get_supabase():
    from supabase import create_client
    return create_client(_secret("SUPABASE_URL"), _secret("SUPABASE_ANON_KEY"))


# -- OAuth helpers ------------------------------------------------------------

def _google_oauth_url() -> str:
    """Generate the Supabase Google OAuth redirect URL."""
    supabase = _get_supabase()
    app_url  = _secret("APP_URL", "http://localhost:8501")
    resp = supabase.auth.sign_in_with_oauth({
        "provider": "google",
        "options":  {"redirect_to": app_url},
    })
    return resp.url


def _exchange_code(code: str):
    """Exchange OAuth code for session. Returns (user_dict | None, error_str)."""
    try:
        supabase = _get_supabase()
        resp     = supabase.auth.exchange_code_for_session({"auth_code": code})
        user     = resp.user
        if not user:
            return None, "No user in session response."
        return {
            "email":  user.email or "",
            "name":   (user.user_metadata or {}).get("full_name", ""),
            "avatar": (user.user_metadata or {}).get("avatar_url", ""),
        }, None
    except Exception as exc:
        return None, str(exc)


# -- Sign-out -----------------------------------------------------------------

def sign_out():
    """Clear session and sign out from Supabase."""
    st.session_state.pop("_auth_user", None)
    st.session_state.pop("authenticated", None)
    st.session_state.pop("user_email", None)
    try:
        if _supabase_configured():
            _get_supabase().auth.sign_out()
    except Exception:
        pass
    st.rerun()


# -- Main auth gate -----------------------------------------------------------

def check_password() -> bool:
    """
    Render the appropriate auth gate and return True when authenticated.

    Priority:
      1. Supabase configured  -> Google OAuth (preferred)
      2. APP_PASSWORD set     -> legacy password gate (beta fallback)
      3. Neither              -> open access (local dev)

    After a successful Google login, session_state["user_email"] is populated
    automatically so the credits system picks it up without any extra wiring.
    """
    # -- Already authenticated ------------------------------------------------
    if st.session_state.get("_auth_user"):
        user = st.session_state["_auth_user"]
        if user.get("email") and not st.session_state.get("user_email"):
            st.session_state["user_email"] = user["email"]
        return True

    # -- Handle OAuth callback code in URL query string -----------------------
    code = st.query_params.get("code")
    # Guard: ignore if it looks like a Razorpay redirect
    if code and _supabase_configured() and "razorpay_payment_id" not in st.query_params:
        with st.spinner("Signing you in..."):
            user_dict, err = _exchange_code(code)
        st.query_params.clear()
        if user_dict:
            st.session_state["_auth_user"]    = user_dict
            st.session_state["authenticated"] = True
            st.session_state["user_email"]    = user_dict["email"]
            st.rerun()
        else:
            st.error(f"Google sign-in failed: {err}", icon="🔒")

    # -- Google login is mandatory regardless of other config ----------------
    return _google_login_page()


# -- Google OAuth login UI ----------------------------------------------------

def _google_login_page() -> bool:
    _, col, _ = st.columns([1, 1.6, 1])
    with col:
        st.markdown(
            "<h1 style='text-align:center;margin-bottom:4px'>🎯 Job Intelligence</h1>"
            "<p style='text-align:center;color:#888;margin-bottom:36px;font-size:1.05rem'>"
            "AI-powered job analysis &middot; CV tailoring &middot; Interview prep</p>",
            unsafe_allow_html=True,
        )
        if not _supabase_configured():
            st.error(
                "Google sign-in is not configured yet. "
                "Please set **SUPABASE_URL**, **SUPABASE_ANON_KEY**, and **APP_URL** "
                "in Streamlit secrets.",
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


# -- Legacy password login UI -------------------------------------------------

def _password_login_page() -> bool:
    expected = _secret("APP_PASSWORD")
    if not expected:
        st.session_state["authenticated"] = True
        return True

    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown(
            "<h2 style='text-align:center;margin-bottom:4px'>🎯 Job Intelligence</h2>"
            "<p style='text-align:center;color:#666;margin-bottom:24px'>"
            "Enter the access password to continue.</p>",
            unsafe_allow_html=True,
        )
        with st.form("_login_form", clear_on_submit=True):
            pwd = st.text_input(
                "Password", type="password",
                placeholder="Access password",
                label_visibility="collapsed",
            )
            submitted = st.form_submit_button(
                "Sign In ->", use_container_width=True, type="primary",
            )
        if submitted:
            if pwd == expected:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password. Please try again.", icon="🔒")

    return False

-- ─────────────────────────────────────────────────────────────────────────────
-- Job Intelligence System — Credits + Payments schema
-- Run this once in: Supabase dashboard → SQL Editor → New query
-- (Run setup_supabase.sql first if you haven't already.)
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. User credits table — one row per email, tracks balance + lifetime stats
CREATE TABLE IF NOT EXISTS user_credits (
    email             TEXT PRIMARY KEY,
    credits           INTEGER      NOT NULL DEFAULT 0,
    total_purchased   INTEGER      NOT NULL DEFAULT 0,
    total_used        INTEGER      NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

ALTER TABLE user_credits DISABLE ROW LEVEL SECURITY;

-- 2. Payment log — one row per Razorpay payment, prevents double-crediting
CREATE TABLE IF NOT EXISTS payment_log (
    payment_id    TEXT PRIMARY KEY,          -- razorpay_payment_id
    email         TEXT NOT NULL,
    runs_added    INTEGER      NOT NULL,
    bundle_label  TEXT         NOT NULL DEFAULT '',
    amount_inr    INTEGER      NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

ALTER TABLE payment_log DISABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS idx_payment_log_email ON payment_log (email);

-- 3. Atomic add_credits() RPC — called by credits.py after a successful payment
--    Increments credits and total_purchased atomically.
--    Uses INSERT ... ON CONFLICT so it works even if the user row doesn't exist yet.
CREATE OR REPLACE FUNCTION add_credits(user_email TEXT, amount INTEGER)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    new_credits INTEGER;
BEGIN
    INSERT INTO user_credits (email, credits, total_purchased, total_used)
    VALUES (lower(trim(user_email)), amount, amount, 0)
    ON CONFLICT (email) DO UPDATE
    SET
        credits         = user_credits.credits + amount,
        total_purchased = user_credits.total_purchased + amount,
        updated_at      = NOW()
    RETURNING credits INTO new_credits;

    RETURN new_credits;
END;
$$;

-- 4. Atomic deduct_credit() RPC — called by credits.py
--    Decrements credits by 1 atomically (safe under concurrent requests).
--    Raises an exception if the user has 0 credits so the caller can surface it.
CREATE OR REPLACE FUNCTION deduct_credit(user_email TEXT)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    new_credits INTEGER;
BEGIN
    UPDATE user_credits
    SET
        credits    = credits - 1,
        total_used = total_used + 1,
        updated_at = NOW()
    WHERE email = lower(trim(user_email))
      AND credits > 0
    RETURNING credits INTO new_credits;

    IF new_credits IS NULL THEN
        RAISE EXCEPTION 'Insufficient credits for %', user_email;
    END IF;

    RETURN new_credits;
END;
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- Done. Add these secrets to Streamlit Cloud → Settings → Secrets:
--
--   APP_PASSWORD        = "your-beta-password"
--   APP_URL             = "https://your-app.streamlit.app"
--   RAZORPAY_KEY_ID     = "rzp_live_..."
--   RAZORPAY_KEY_SECRET = "your-razorpay-key-secret"
-- ─────────────────────────────────────────────────────────────────────────────

-- ─────────────────────────────────────────────────────────────────────────────
-- Job Intelligence System — Supabase one-time setup
-- Run this entire script once in: Supabase dashboard → SQL Editor → New query
-- ─────────────────────────────────────────────────────────────────────────────

-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create the JD entries table
CREATE TABLE IF NOT EXISTS jd_entries (
    id                      TEXT PRIMARY KEY,
    title                   TEXT NOT NULL DEFAULT '',
    company                 TEXT NOT NULL DEFAULT '',
    location                TEXT NOT NULL DEFAULT '',
    platform                TEXT NOT NULL DEFAULT '',
    posting_date            TEXT NOT NULL DEFAULT '',
    job_type                TEXT NOT NULL DEFAULT '',
    experience_level        TEXT NOT NULL DEFAULT '',
    jd_text                 TEXT NOT NULL,
    jd_text_hash            TEXT NOT NULL DEFAULT '',
    cached_jd_analysis      TEXT NOT NULL DEFAULT '',
    cached_company_research TEXT NOT NULL DEFAULT '',
    user_email              TEXT NOT NULL DEFAULT '',  -- owner of this JD entry
    embedding               vector(512),               -- Voyage AI voyage-3-lite dim
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2a. Migration: add user_email to existing tables (safe to run on existing DB)
ALTER TABLE jd_entries ADD COLUMN IF NOT EXISTS user_email TEXT NOT NULL DEFAULT '';

-- 3. Disable Row Level Security so the anon key can INSERT/UPDATE/SELECT
--    (This is a single-user private app — RLS is not needed)
ALTER TABLE jd_entries DISABLE ROW LEVEL SECURITY;

-- 4. Indexes for fast metadata filtering
CREATE INDEX IF NOT EXISTS idx_jd_exp_level  ON jd_entries (experience_level);
CREATE INDEX IF NOT EXISTS idx_jd_job_type   ON jd_entries (job_type);
CREATE INDEX IF NOT EXISTS idx_jd_text_hash  ON jd_entries (jd_text_hash);
CREATE INDEX IF NOT EXISTS idx_jd_user_email ON jd_entries (user_email);

-- 5. IVFFlat index for fast approximate nearest-neighbour search
--    (Only beneficial once you have 1000+ rows. Harmless before that.)
CREATE INDEX IF NOT EXISTS idx_jd_embedding
    ON jd_entries USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- 5. RPC function called by jd_database.py → find_similar_jds()
CREATE OR REPLACE FUNCTION match_jds(
    query_embedding    vector(512),
    match_threshold    float,
    match_count        int,
    exclude_id         text    DEFAULT NULL,
    filter_exp_levels  text[]  DEFAULT NULL,
    filter_job_type    text    DEFAULT NULL,
    filter_user_email  text    DEFAULT NULL   -- '' or NULL = no filter (all users)
)
RETURNS TABLE (
    id               text,
    title            text,
    company          text,
    location         text,
    platform         text,
    posting_date     text,
    job_type         text,
    experience_level text,
    similarity       float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        j.id,
        j.title,
        j.company,
        j.location,
        j.platform,
        j.posting_date,
        j.job_type,
        j.experience_level,
        (1 - (j.embedding <=> query_embedding))::float AS similarity
    FROM jd_entries j
    WHERE
        j.embedding IS NOT NULL
        AND (exclude_id IS NULL           OR j.id != exclude_id)
        AND (filter_exp_levels IS NULL    OR j.experience_level = ANY(filter_exp_levels))
        AND (
            filter_job_type IS NULL
            OR j.job_type = ''
            OR j.job_type = filter_job_type
        )
        AND (
            filter_user_email IS NULL
            OR filter_user_email = ''
            OR j.user_email = filter_user_email
        )
        AND (1 - (j.embedding <=> query_embedding)) >= match_threshold
    ORDER BY j.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- Done. Add these secrets to Streamlit Cloud → Settings → Secrets:
--
--   SUPABASE_URL   = "https://xxxxxxxxxxxx.supabase.co"
--   SUPABASE_KEY   = "your-anon-or-service-role-key"
--   VOYAGE_API_KEY = "your-voyage-api-key"
-- ─────────────────────────────────────────────────────────────────────────────

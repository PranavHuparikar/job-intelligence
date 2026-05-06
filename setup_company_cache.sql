-- setup_company_cache.sql
-- Run once in the Supabase SQL editor to create the company research cache table.
-- Safe to re-run: uses IF NOT EXISTS / DROP IF EXISTS throughout.

CREATE TABLE IF NOT EXISTS company_research_cache (
    id          BIGSERIAL    PRIMARY KEY,
    cache_key   TEXT         NOT NULL UNIQUE,
    company     TEXT         NOT NULL,
    research    TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_company_cache_key
    ON company_research_cache (cache_key);

CREATE INDEX IF NOT EXISTS idx_company_cache_created_at
    ON company_research_cache (created_at);

ALTER TABLE company_research_cache ENABLE ROW LEVEL SECURITY;

-- DROP first so re-runs don't error
DROP POLICY IF EXISTS "allow_read"          ON company_research_cache;
DROP POLICY IF EXISTS "allow_insert_update" ON company_research_cache;

CREATE POLICY "allow_read"
    ON company_research_cache FOR SELECT USING (true);

CREATE POLICY "allow_insert_update"
    ON company_research_cache FOR ALL
    USING (true) WITH CHECK (true);

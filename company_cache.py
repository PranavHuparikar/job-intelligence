"""
company_cache.py — 24-hour Supabase cache for company research (T1) output.

Why this exists:
  Company research is the most expensive task per run: 7 web searches + optional
  scrape + LLM synthesis.  The result changes slowly (company culture, salary
  bands, interview format don't shift daily).  Caching it for 24 hours means
  repeat analyses of the same company (different CVs, different roles at the
  same company) skip T1 entirely, saving ~2–3 min and ~$0.05 per run.

Cache key: normalised company name only.
  Role title and experience level are NOT part of the key because T1's output
  is a generic company report (salary table covers all bands, culture is
  company-wide).  T3 (interview coach) already calibrates question difficulty
  from the experience_level variable — the company report itself is reusable.

TTL: 24 hours (configurable via COMPANY_CACHE_TTL_HOURS env var).
"""

import os
from datetime import datetime, timedelta, timezone

_TTL_HOURS = int(os.environ.get("COMPANY_CACHE_TTL_HOURS", "24"))


def _client():
    """Return a Supabase client, or None if not configured."""
    try:
        from supabase import create_client
        url = os.environ.get("SUPABASE_URL", "")
        key = (
            os.environ.get("SUPABASE_SERVICE_KEY")
            or os.environ.get("SUPABASE_ANON_KEY", "")
        )
        if url and key:
            return create_client(url, key)
    except Exception:
        pass
    return None


def _cache_key(company: str) -> str:
    """Normalised cache key: lowercase, stripped."""
    return company.strip().lower()


def get_cached_research(company: str) -> str | None:
    """
    Return cached company research if a fresh entry exists (< TTL hours old).
    Returns None on any failure so the caller always falls back gracefully.
    """
    if not company or not company.strip():
        return None
    try:
        sb = _client()
        if sb is None:
            return None
        key    = _cache_key(company)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=_TTL_HOURS)
        ).isoformat()
        rows = (
            sb.table("company_research_cache")
            .select("research, created_at")
            .eq("cache_key", key)
            .gt("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if rows.data:
            return rows.data[0]["research"]
    except Exception:
        pass
    return None


def cache_research(company: str, research: str) -> None:
    """
    Upsert company research into the cache.
    Silently swallows any errors — caching is best-effort.
    """
    if not company or not research:
        return
    try:
        sb = _client()
        if sb is None:
            return
        key = _cache_key(company)
        sb.table("company_research_cache").upsert(
            {
                "cache_key":  key,
                "company":    company.strip(),
                "research":   research,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="cache_key",
        ).execute()
    except Exception:
        pass

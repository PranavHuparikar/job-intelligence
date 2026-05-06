"""
jd_database.py - JD storage and similarity search for the Job Intelligence System.

Backend selection (auto-detected at runtime):
  ┌─ Supabase + Voyage AI ─────────────────────────────────────────────────────┐
  │  Requires env vars (or Streamlit secrets):                                  │
  │    SUPABASE_URL   — Project URL from Supabase dashboard                     │
  │    SUPABASE_KEY   — anon/service-role key from Supabase dashboard           │
  │    VOYAGE_API_KEY — API key from dash.voyageai.com                          │
  │  Persistent across restarts. Production-grade vector search via pgvector.   │
  └─────────────────────────────────────────────────────────────────────────────┘
  ┌─ SQLite fallback ───────────────────────────────────────────────────────────┐
  │  Used automatically when Supabase env vars are absent (local dev).          │
  │  Embeddings are disabled on Streamlit Cloud (sentence-transformers too      │
  │  heavy). find_similar_jds() returns [] gracefully when model unavailable.   │
  └─────────────────────────────────────────────────────────────────────────────┘

Supabase one-time setup: run setup_supabase.sql in the Supabase SQL Editor.

Voyage AI model: voyage-3-lite  (512 dimensions, fast, free tier: 50M tokens/mo)
"""

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

# ── Config ────────────────────────────────────────────────────────────────────

_DB_PATH         = Path(os.getenv("JD_DB_PATH", "saved_inputs/jd_store.db"))
_EMBED_CHAR_CAP  = 3000


def _normalize_jd_for_embedding(text: str) -> str:
    """
    Strip intro/preamble from a JD before embedding so that two JDs with the
    same responsibilities/skills but different intros produce near-identical
    vectors.  Falls back to full text when no structure is detected.

    Why: "We are a fast-growing startup..." intro paragraphs add noise that
    pushes cosine similarity below 0.95 even for near-identical JDs.
    """
    _CONTENT_SIGNALS = [
        r"responsibilities", r"what you.ll do", r"what you will do",
        r"key responsibilities", r"role overview", r"about the role",
        r"your role", r"the role", r"position overview",
        r"requirements", r"qualifications", r"what we.re looking for",
        r"what you.re looking for", r"skills required", r"skills & experience",
        r"technical skills", r"you should have", r"you will have",
        r"minimum qualifications", r"preferred qualifications",
        r"job requirements", r"key requirements", r"must have",
        r"essential skills", r"experience required",
    ]
    lower = text.lower()
    earliest_pos = len(text)
    for signal in _CONTENT_SIGNALS:
        m = re.search(signal, lower)
        if m:
            line_start = text.rfind("\n", 0, m.start())
            pos = line_start if line_start >= 0 else m.start()
            earliest_pos = min(earliest_pos, pos)

    # Only strip if intro is meaningfully long and structure was found
    if 200 < earliest_pos < int(len(text) * 0.7):
        normalized = text[earliest_pos:].strip()
    else:
        normalized = text

    return normalized[:_EMBED_CHAR_CAP]


# ── Keyword & skill extraction for hybrid similarity scoring ──────────────────

# High-value technical terms — matched exactly (case-insensitive substring)
_TECH_TERMS = {
    # Languages
    "python","java","javascript","typescript","c++","c#","golang","go","rust",
    "scala","kotlin","swift","ruby","php","r programming","matlab","bash","shell",
    # AI / ML
    "tensorflow","pytorch","keras","scikit-learn","sklearn","xgboost","lightgbm",
    "huggingface","transformers","llm","nlp","computer vision","deep learning",
    "machine learning","neural network","reinforcement learning","bert","gpt",
    "stable diffusion","langchain","rag","vector database","embedding",
    # Data engineering
    "spark","hadoop","kafka","airflow","dbt","databricks","snowflake","redshift",
    "bigquery","hive","pandas","numpy","dask","flink","luigi","prefect","dagster",
    # Cloud & infra
    "aws","gcp","azure","kubernetes","docker","terraform","ansible","helm",
    "ci/cd","jenkins","github actions","gitlab ci","s3","ec2","lambda","gke",
    "cloudformation","pulumi","prometheus","grafana","datadog",
    # Databases
    "postgresql","mysql","mongodb","redis","elasticsearch","cassandra","dynamodb",
    "sqlite","neo4j","pinecone","weaviate","qdrant","chroma","pgvector","milvus",
    # Web / backend
    "react","angular","vue","node.js","django","flask","fastapi","spring boot",
    "express","graphql","rest api","grpc","microservices","websocket",
    # Practices
    "agile","scrum","devops","mlops","llmops","system design","distributed systems",
    "unit testing","tdd","bdd","code review","git","github","gitlab",
    # Data science
    "sql","tableau","power bi","looker","jupyter","statistics","regression",
    "classification","clustering","feature engineering","a/b testing","etl","elt",
}

_STOP_WORDS = {
    "with","from","this","that","will","have","been","they","their","would",
    "could","should","about","which","when","your","into","more","also","work",
    "using","strong","good","required","must","ability","excellent","years",
    "experience","team","role","position","candidate","company","looking",
    "please","apply","join","working","including","such","well","within","across",
    "help","build","make","take","provide","ensure","maintain","support","manage",
}


def _extract_jd_keywords(text: str) -> set:
    """
    Extract meaningful keywords from JD text.
    Returns a set of normalised terms covering: tech skills, tools, and
    significant noun-like words (length ≥ 4, not stop words).
    """
    lower = text.lower()
    found = set()

    # 1. Exact match of pre-defined high-value tech terms
    for term in _TECH_TERMS:
        if term in lower:
            found.add(term)

    # 2. Extract single words (length ≥ 4, alphanumeric + . + #, not stop words)
    words = re.findall(r"\b[a-z][a-z0-9+#.]{3,}\b", lower)
    for w in words:
        if w not in _STOP_WORDS:
            found.add(w)

    return found


def _keyword_overlap_score(kw_query: set, jd_text_stored: str) -> float:
    """
    Weighted Jaccard similarity between query keywords and stored JD keywords.
    Tech terms count 3× their weight in the intersection/union so that specific
    skill matches dominate over generic word overlap.
    """
    kw_stored = _extract_jd_keywords(jd_text_stored)
    if not kw_query or not kw_stored:
        return 0.0

    common      = kw_query & kw_stored
    all_terms   = kw_query | kw_stored

    # Weight tech terms 3× in numerator and denominator
    def _weighted(terms: set) -> float:
        return sum(3.0 if t in _TECH_TERMS else 1.0 for t in terms)

    w_common = _weighted(common)
    w_all    = _weighted(all_terms)
    return w_common / w_all if w_all > 0 else 0.0
_TOP_K_DEFAULT   = 5
_MIN_SIMILARITY  = 0.50
_VOYAGE_MODEL    = "voyage-3-lite"   # 512-dim, free tier generous
_VOYAGE_DIM      = 512

_EXP_BAND: Dict[str, int] = {
    "entry":  0,
    "mid":    1,
    "senior": 2,
    "":       -1,
}


# ── Backend detection ─────────────────────────────────────────────────────────

def _supabase_configured() -> bool:
    """True if all three Supabase+Voyage env vars are present."""
    # Try Streamlit secrets first (cloud deployment)
    try:
        import streamlit as st
        s = st.secrets
        return (
            bool(s.get("SUPABASE_URL"))
            and bool(s.get("SUPABASE_KEY"))
            and bool(s.get("VOYAGE_API_KEY"))
        )
    except Exception:
        pass
    # Fall back to environment variables (local dev with .env)
    return (
        bool(os.getenv("SUPABASE_URL"))
        and bool(os.getenv("SUPABASE_KEY"))
        and bool(os.getenv("VOYAGE_API_KEY"))
    )


def _get_secret(key: str) -> str:
    """Read a secret from Streamlit secrets or os.environ."""
    try:
        import streamlit as st
        val = st.secrets.get(key, "")
        if val:
            return val
    except Exception:
        pass
    return os.getenv(key, "")


# ── Supabase client (lazy singleton, thread-safe) ─────────────────────────────

_supabase_client = None
_supabase_lock   = threading.Lock()


def _get_supabase():
    global _supabase_client
    if _supabase_client is None:
        with _supabase_lock:
            if _supabase_client is None:   # double-checked locking
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
                        "SUPABASE_URL and SUPABASE_KEY must be set."
                    )
                _supabase_client = create_client(url, key)
    return _supabase_client


# ── Voyage AI embedder (lazy singleton, thread-safe) ─────────────────────────

_voyage_client = None
_voyage_lock   = threading.Lock()


def _get_voyage():
    global _voyage_client
    if _voyage_client is None:
        with _voyage_lock:
            if _voyage_client is None:     # double-checked locking
                try:
                    import voyageai
                except ImportError:
                    raise ImportError(
                        "voyageai package not installed. Run: pip install voyageai"
                    )
                api_key = _get_secret("VOYAGE_API_KEY")
                if not api_key:
                    raise EnvironmentError("VOYAGE_API_KEY must be set.")
                _voyage_client = voyageai.Client(api_key=api_key)
    return _voyage_client


def _embed_voyage(text: str) -> List[float]:
    """Embed text via Voyage AI API. Returns 512-dim normalised vector."""
    client = _get_voyage()
    result = client.embed(
        [text[:_EMBED_CHAR_CAP]],
        model=_VOYAGE_MODEL,
        input_type="document",
    )
    return result.embeddings[0]


# ── Local SQLite helpers (fallback) ──────────────────────────────────────────

def _embed_local(text: str) -> List[float]:
    """Embed via local sentence-transformers (only available in local dev)."""
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
        vec = model.encode(text[:_EMBED_CHAR_CAP], normalize_embeddings=True)
        return vec.tolist()
    except ImportError:
        raise ImportError("sentence-transformers not installed.")


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    norm = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / norm) if norm > 0 else 0.0


def _init_sqlite() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS jd_metadata ("
        "id TEXT PRIMARY KEY, "
        "title TEXT NOT NULL DEFAULT '', "
        "company TEXT NOT NULL DEFAULT '', "
        "location TEXT NOT NULL DEFAULT '', "
        "platform TEXT NOT NULL DEFAULT '', "
        "posting_date TEXT NOT NULL DEFAULT '', "
        "job_type TEXT NOT NULL DEFAULT '', "
        "experience_level TEXT NOT NULL DEFAULT '', "
        "jd_text TEXT NOT NULL, "
        "jd_text_hash TEXT DEFAULT '', "
        "cached_jd_analysis TEXT DEFAULT '', "
        "cached_company_research TEXT DEFAULT '', "
        "created_at TEXT NOT NULL"
        ")"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS jd_embeddings ("
        "jd_id TEXT PRIMARY KEY, "
        "embedding TEXT NOT NULL, "
        "FOREIGN KEY (jd_id) REFERENCES jd_metadata(id) ON DELETE CASCADE"
        ")"
    )
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_exp_level ON jd_metadata(experience_level)",
        "CREATE INDEX IF NOT EXISTS idx_job_type ON jd_metadata(job_type)",
        "CREATE INDEX IF NOT EXISTS idx_jd_hash ON jd_metadata(jd_text_hash)",
    ]:
        try:
            c.execute(idx)
        except Exception:
            pass
    # Idempotent column migrations for older DBs
    for col_sql in [
        "ALTER TABLE jd_metadata ADD COLUMN jd_text_hash TEXT DEFAULT ''",
        "ALTER TABLE jd_metadata ADD COLUMN cached_jd_analysis TEXT DEFAULT ''",
        "ALTER TABLE jd_metadata ADD COLUMN cached_company_research TEXT DEFAULT ''",
    ]:
        try:
            c.execute(col_sql)
        except Exception:
            pass
    conn.commit()
    conn.close()


# ── Shared helpers ────────────────────────────────────────────────────────────

def _jd_hash(jd_text: str, company: str = "") -> str:
    """Hash is keyed on both JD text AND company name.
    Same JD submitted for a different company must get a fresh cache entry."""
    normalised = " ".join(jd_text.lower().split())
    company_norm = company.lower().strip()
    return hashlib.sha256(
        f"{normalised}|{company_norm}".encode("utf-8")
    ).hexdigest()


def _strip_truncation_markers(text: str) -> str:
    return re.sub(
        r"\n\n\[(?:JD Analysis|Company Research) truncated for context efficiency\]",
        "", text or ""
    ).strip()


# ═══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def init_db() -> None:
    """
    Initialise the database backend.
    Supabase: no-op (schema created via setup_supabase.sql).
    SQLite:   creates tables if they don't exist.
    Safe to call on every access.
    """
    if not _supabase_configured():
        _init_sqlite()


# ── Analysis cache ────────────────────────────────────────────────────────────

def get_cached_analysis(jd_text: str, company: str = "") -> Optional[tuple]:
    """
    Return (jd_analysis, company_research) for a previously analysed JD,
    or None if not cached.
    Company name is part of the cache key — same JD for a different company
    will NOT return a stale cache hit.
    """
    h = _jd_hash(jd_text, company)

    if _supabase_configured():
        try:
            sb = _get_supabase()
            res = (
                sb.table("jd_entries")
                .select("cached_jd_analysis, cached_company_research")
                .eq("jd_text_hash", h)
                .limit(1)
                .execute()
            )
            if res.data:
                row = res.data[0]
                jd_a = _strip_truncation_markers(row.get("cached_jd_analysis", ""))
                co_r = _strip_truncation_markers(row.get("cached_company_research", ""))
                if jd_a and co_r:
                    return jd_a, co_r
        except Exception as e:
            print(f"  [jd_database WARNING] Supabase cache lookup failed: {e}")
        return None

    # SQLite fallback
    _init_sqlite()
    conn = sqlite3.connect(_DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT cached_jd_analysis, cached_company_research "
        "FROM jd_metadata WHERE jd_text_hash = ? LIMIT 1",
        (h,),
    )
    row = c.fetchone()
    conn.close()
    if row and row[0] and row[1]:
        return _strip_truncation_markers(row[0]), _strip_truncation_markers(row[1])
    return None


def store_analysis(jd_id: str, jd_analysis: str, company_research: str) -> None:
    """Persist T0 and T1 outputs so future identical JDs skip those agents."""
    if _supabase_configured():
        try:
            sb = _get_supabase()
            # Fetch jd_text + company so we can recompute the correct hash
            res = (
                sb.table("jd_entries")
                .select("jd_text, company")
                .eq("id", jd_id)
                .limit(1)
                .execute()
            )
            if res.data:
                h = _jd_hash(res.data[0]["jd_text"], res.data[0].get("company", ""))
                sb.table("jd_entries").update({
                    "jd_text_hash":          h,
                    "cached_jd_analysis":    jd_analysis,
                    "cached_company_research": company_research,
                }).eq("id", jd_id).execute()
        except Exception as e:
            print(f"  [jd_database WARNING] Supabase store_analysis failed: {e}")
        return

    # SQLite fallback
    _init_sqlite()
    conn = sqlite3.connect(_DB_PATH)
    c = conn.cursor()
    c.execute("SELECT jd_text, company FROM jd_metadata WHERE id = ?", (jd_id,))
    row = c.fetchone()
    if row:
        h = _jd_hash(row[0], row[1] if len(row) > 1 else "")
        c.execute(
            "UPDATE jd_metadata "
            "SET jd_text_hash = ?, cached_jd_analysis = ?, cached_company_research = ? "
            "WHERE id = ?",
            (h, jd_analysis, company_research, jd_id),
        )
        conn.commit()
    conn.close()


# ── Save JD ───────────────────────────────────────────────────────────────────

def save_jd(
    jd_text: str,
    title: str = "",
    company: str = "",
    location: str = "",
    platform: str = "",
    posting_date: str = "",
    job_type: str = "",
    experience_level: str = "",
    jd_id: Optional[str] = None,
) -> str:
    """
    Save a JD and generate its embedding.
    Returns the jd_id. Embedding failure is non-fatal.
    """
    if jd_id is None:
        jd_id = str(uuid.uuid4())[:12]

    h = _jd_hash(jd_text, company)
    now = datetime.now().isoformat()

    if _supabase_configured():
        try:
            sb = _get_supabase()
            record: Dict[str, Any] = {
                "id":               jd_id,
                "title":            title.strip(),
                "company":          company.strip(),
                "location":         location.strip(),
                "platform":         platform.strip(),
                "posting_date":     posting_date.strip(),
                "job_type":         job_type.strip(),
                "experience_level": experience_level.strip(),
                "jd_text":          jd_text,
                "jd_text_hash":     h,
                "created_at":       now,
            }
            # Generate embedding via Voyage AI
            try:
                embedding = _embed_voyage(_normalize_jd_for_embedding(jd_text))
                # Pass as list — supabase-py v2 serialises List[float] correctly
                # for pgvector vector(512) columns
                record["embedding"] = list(embedding)
            except Exception as e:
                print(f"  [jd_database WARNING] Voyage embedding failed: {e}")
                print("  JD saved without embedding — won't appear in similarity search.")

            sb.table("jd_entries").upsert(record).execute()
        except Exception as e:
            print(f"  [jd_database WARNING] Supabase save_jd failed: {e}")
            # Re-raise so the caller can surface it in the UI
            raise
        return jd_id

    # SQLite fallback
    _init_sqlite()
    conn = sqlite3.connect(_DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO jd_metadata "
        "(id, title, company, location, platform, posting_date, "
        "job_type, experience_level, jd_text, jd_text_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            jd_id,
            title.strip(), company.strip(), location.strip(),
            platform.strip(), posting_date.strip(),
            job_type.strip(), experience_level.strip(),
            jd_text, h, now,
        )
    )
    try:
        embedding = _embed_local(jd_text)
        c.execute(
            "INSERT OR REPLACE INTO jd_embeddings (jd_id, embedding) VALUES (?, ?)",
            (jd_id, json.dumps(embedding))
        )
    except Exception as e:
        print(f"  [jd_database WARNING] Local embedding failed: {e}")
        print("  JD metadata saved without embedding.")
    conn.commit()
    conn.close()
    return jd_id


# ── Similarity search ─────────────────────────────────────────────────────────

def find_similar_jds(
    jd_text: str,
    top_k: int = _TOP_K_DEFAULT,
    exclude_id: Optional[str] = None,
    experience_level: Optional[str] = None,
    job_type: Optional[str] = None,
    min_similarity: float = _MIN_SIMILARITY,
) -> List[Dict[str, Any]]:
    """
    Return the top-k most similar JDs, sorted by similarity descending.
    Returns [] gracefully if embeddings unavailable or DB empty.
    """
    if _supabase_configured():
        # ── Exact-match fast path: same hash → 1.0 similarity ─────────────────
        try:
            sb = _get_supabase()
            query_hash = _jd_hash(jd_text, "")  # company-agnostic hash for query
            # Also try company-keyed hash if we can infer it
            exact_res = (
                sb.table("jd_entries")
                .select("id, title, company, location, platform, posting_date, job_type, experience_level")
                .eq("jd_text_hash", query_hash)
                .limit(1)
                .execute()
            )
            exact_matches = []
            if exact_res.data:
                for row in exact_res.data:
                    if exclude_id and row.get("id") == exclude_id:
                        continue
                    exact_matches.append({
                        "id":               row.get("id", ""),
                        "title":            row.get("title") or "Unknown Title",
                        "company":          row.get("company") or "Unknown Company",
                        "location":         row.get("location") or "Not specified",
                        "platform":         row.get("platform") or "Not specified",
                        "posting_date":     row.get("posting_date") or "Not specified",
                        "job_type":         row.get("job_type") or "Not specified",
                        "experience_level": row.get("experience_level") or "Not specified",
                        "similarity":       1.0,
                    })
        except Exception:
            exact_matches = []

        try:
            query_vec = _embed_voyage(_normalize_jd_for_embedding(jd_text))
        except Exception as e:
            print(f"  [jd_database WARNING] Could not embed query: {e}")
            return exact_matches  # return exact matches even if vector search fails

        # Build exp-level filter list
        exp_levels = None
        if experience_level and experience_level in _EXP_BAND:
            band = _EXP_BAND[experience_level]
            if band >= 0:
                exp_levels = [
                    k for k, v in _EXP_BAND.items()
                    if v >= 0 and abs(v - band) <= 1
                ]

        try:
            sb = _get_supabase()
            rpc_args: Dict[str, Any] = {
                "query_embedding": query_vec,
                "match_threshold":  min_similarity,
                "match_count":      top_k,
            }
            if exclude_id:
                rpc_args["exclude_id"] = exclude_id
            if exp_levels:
                rpc_args["filter_exp_levels"] = exp_levels
            if job_type and job_type.lower() not in ("", "not specified"):
                rpc_args["filter_job_type"] = job_type

            res = sb.rpc("match_jds", rpc_args).execute()
            results = []
            seen_ids = {m["id"] for m in exact_matches}
            for row in (res.data or []):
                rid = row.get("id", "")
                if rid in seen_ids:
                    continue  # already in exact_matches with 1.0 similarity
                results.append({
                    "id":               rid,
                    "title":            row.get("title") or "Unknown Title",
                    "company":          row.get("company") or "Unknown Company",
                    "location":         row.get("location") or "Not specified",
                    "platform":         row.get("platform") or "Not specified",
                    "posting_date":     row.get("posting_date") or "Not specified",
                    "job_type":         row.get("job_type") or "Not specified",
                    "experience_level": row.get("experience_level") or "Not specified",
                    "similarity":       round(float(row.get("similarity", 0)), 3),
                })
            # ── Hybrid re-scoring: blend vector + keyword overlap ─────────────
            if results:
                try:
                    ids = [r["id"] for r in results]
                    texts_res = (
                        sb.table("jd_entries")
                        .select("id, jd_text")
                        .in_("id", ids)
                        .execute()
                    )
                    id_to_text = {
                        row["id"]: row["jd_text"]
                        for row in (texts_res.data or [])
                    }
                    query_kw = _extract_jd_keywords(jd_text)
                    for r in results:
                        stored_text = id_to_text.get(r["id"], "")
                        if stored_text:
                            kw_score = _keyword_overlap_score(query_kw, stored_text)
                            # 40% vector semantics + 60% keyword/skill overlap
                            r["similarity"] = round(
                                0.40 * r["similarity"] + 0.60 * kw_score, 3
                            )
                    results.sort(key=lambda x: x["similarity"], reverse=True)
                except Exception as _ke:
                    print(f"  [jd_database] Keyword re-scoring failed (using vector only): {_ke}")

            # Exact matches always appear first, then vector results
            combined = exact_matches + results
            return combined[:top_k]
        except Exception as e:
            print(f"  [jd_database WARNING] Supabase similarity search failed: {e}")
            return exact_matches  # still return exact matches if vector search fails

    # SQLite fallback
    _init_sqlite()
    conn = sqlite3.connect(_DB_PATH)
    c = conn.cursor()
    query = (
        "SELECT m.id, m.title, m.company, m.location, m.platform, "
        "m.posting_date, m.job_type, m.experience_level, e.embedding "
        "FROM jd_metadata m "
        "JOIN jd_embeddings e ON m.id = e.jd_id WHERE 1=1"
    )
    params: List[Any] = []
    if exclude_id:
        query += " AND m.id != ?"
        params.append(exclude_id)
    if experience_level and experience_level in _EXP_BAND:
        band = _EXP_BAND[experience_level]
        if band >= 0:
            nearby = [k for k, v in _EXP_BAND.items() if abs(v - band) <= 1 and v >= 0]
            query += " AND m.experience_level IN (" + ",".join("?" * len(nearby)) + ")"
            params.extend(nearby)
    if job_type and job_type.lower() not in ("", "not specified"):
        query += " AND (m.job_type = ? OR m.job_type = '')"
        params.append(job_type)
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    if not rows:
        return []
    try:
        query_vec = _embed_local(jd_text)
    except Exception as e:
        print(f"  [jd_database WARNING] Could not embed query: {e}")
        return []
    scored: List[Dict[str, Any]] = []
    for row in rows:
        jd_id, title, company, location, platform, posting_date, jt, exp_level, emb_json = row
        try:
            emb = json.loads(emb_json)
            score = _cosine_similarity(query_vec, emb)
            if score >= min_similarity:
                scored.append({
                    "id":               jd_id,
                    "title":            title or "Unknown Title",
                    "company":          company or "Unknown Company",
                    "location":         location or "Not specified",
                    "platform":         platform or "Not specified",
                    "posting_date":     posting_date or "Not specified",
                    "job_type":         jt or "Not specified",
                    "experience_level": exp_level or "Not specified",
                    "similarity":       round(score, 3),
                })
        except Exception:
            continue
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


# ── Utility queries ───────────────────────────────────────────────────────────

def get_jd_count() -> int:
    """Return total number of JDs stored."""
    if _supabase_configured():
        try:
            sb = _get_supabase()
            res = sb.table("jd_entries").select("id", count="exact").execute()
            return res.count or 0
        except Exception as e:
            print(f"  [jd_database WARNING] Supabase count failed: {e}")
            return 0
    # SQLite fallback
    _init_sqlite()
    conn = sqlite3.connect(_DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM jd_metadata")
    count = c.fetchone()[0]
    conn.close()
    return count


def get_jd_by_id(jd_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single JD record by ID."""
    if _supabase_configured():
        try:
            sb = _get_supabase()
            res = (
                sb.table("jd_entries")
                .select(
                    "id, title, company, location, platform, "
                    "posting_date, job_type, experience_level, jd_text, created_at"
                )
                .eq("id", jd_id)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None
        except Exception as e:
            print(f"  [jd_database WARNING] Supabase get_jd_by_id failed: {e}")
            return None
    # SQLite fallback
    _init_sqlite()
    conn = sqlite3.connect(_DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, title, company, location, platform, posting_date, "
        "job_type, experience_level, jd_text, created_at "
        "FROM jd_metadata WHERE id = ?",
        (jd_id,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    keys = [
        "id", "title", "company", "location", "platform",
        "posting_date", "job_type", "experience_level", "jd_text", "created_at"
    ]
    return dict(zip(keys, row))

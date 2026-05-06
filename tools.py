"""
tools.py — Custom tools for the Job Intelligence agents.
Web search via Tavily (agent-native API, 1000 searches/month free) + lightweight web scraper.

Performance tuning:
- max_results 3  (concise payload per search)
- scrape limit 2500 chars  (60% fewer tokens vs. 6000)
- Tavily has no rate-limit issues unlike DuckDuckGo
"""

import langchain as _lc
if not hasattr(_lc, "verbose"):
    _lc.verbose = False
if not hasattr(_lc, "debug"):
    _lc.debug = False

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import os

import requests
from bs4 import BeautifulSoup
from crewai.tools import tool


# ── Constants ─────────────────────────────────────────────────────────────────

_ALLOWED_SCRAPE_DOMAINS = {
    "glassdoor.com", "ambitionbox.com", "linkedin.com", "indeed.com",
    "naukri.com", "levels.fyi", "payscale.com", "comparably.com",
    "teamblind.com", "fishbowlapp.com", "reddit.com", "techcrunch.com",
    "economictimes.indiatimes.com", "moneycontrol.com",
    "yourstory.com", "crunchbase.com",
}

_CACHE_DIR           = Path(".cache/company_research")
_CACHE_TTL_DAYS      = 30
_SEARCH_MAX_RESULTS  = 3    # top-3 results per query
_SCRAPE_CHAR_LIMIT   = 2500 # cap scrape output to keep tokens low


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_allowed_url(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower().lstrip("www.")
        return any(netloc == d or netloc.endswith(f".{d}") for d in _ALLOWED_SCRAPE_DOMAINS)
    except Exception:
        return False


def _cache_key(query: str) -> str:
    month = datetime.now().strftime("%Y-%m")
    return hashlib.md5(f"{query.lower().strip()}:{month}".encode()).hexdigest()


def _cache_get(query: str) -> str | None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = _CACHE_DIR / f"{_cache_key(query)}.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        age = (datetime.now() - datetime.fromisoformat(data["timestamp"])).days
        if age <= _CACHE_TTL_DAYS:
            return data["result"]
    except Exception:
        pass
    return None


def _cache_set(query: str, result: str) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = _CACHE_DIR / f"{_cache_key(query)}.json"
    # Write to a temp file then atomically replace to avoid concurrent-write corruption
    tmp = f.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps({"timestamp": datetime.now().isoformat(), "result": result}),
            encoding="utf-8",
        )
        tmp.replace(f)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


# ── Web Search Tool ───────────────────────────────────────────────────────────

def _get_tavily_client():
    """Return a TavilyClient, raising a clear error if the package is missing."""
    try:
        from tavily import TavilyClient
    except ImportError:
        raise ImportError(
            "tavily-python is required. Run: pip install tavily-python"
        )
    # Read from Streamlit secrets first (cloud), fall back to env var (local)
    api_key = ""
    try:
        import streamlit as st
        api_key = st.secrets.get("TAVILY_API_KEY", "")
    except Exception:
        pass
    api_key = api_key or os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "TAVILY_API_KEY not set. Add it to your .env file or Streamlit secrets."
        )
    return TavilyClient(api_key=api_key)


@tool("web_search")
def web_search(query: str) -> str:
    """
    Search the web using Tavily (agent-native search API). Use this to find
    company information, salary data, employee reviews, recent news, or
    interview experiences.
    Input: a plain-text search query string.
    Output: top 3 search results with title, URL, and snippet.
    Results are cached for 30 days — identical queries return instantly.
    IMPORTANT: run at most 5 searches total per task, then synthesise.
    """
    cached = _cache_get(query)
    if cached:
        print(f"  [cache hit] {query[:60]}")
        return cached

    try:
        client = _get_tavily_client()
        response = client.search(
            query=query,
            max_results=_SEARCH_MAX_RESULTS,
            search_depth="basic",
        )
        raw_results = response.get("results", [])
        if not raw_results:
            return (
                f"[No results for '{query}'. DO NOT retry — use your knowledge "
                "to complete this section.]"
            )

        results = []
        for r in raw_results:
            results.append(
                f"Title: {r.get('title', '')}\n"
                f"URL: {r.get('url', '')}\n"
                f"Snippet: {r.get('content', '')}\n"
            )

        output = "\n---\n".join(results)
        _cache_set(query, output)
        return output

    except EnvironmentError as e:
        # API key missing — surface clearly
        return f"[Search error: {e}]"
    except Exception as e:
        return (
            f"[Search failed for '{query}': {e}. DO NOT retry. "
            "Use your existing knowledge to write this section.]"
        )


# ── Web Scraper Tool ──────────────────────────────────────────────────────────

@tool("scrape_page")
def scrape_page(url: str) -> str:
    """
    Scrape the text content of a webpage.
    Input: a URL from a supported domain (glassdoor.com, ambitionbox.com,
    linkedin.com, indeed.com, naukri.com, reddit.com, crunchbase.com, etc.).
    Output: cleaned page text (up to 2500 characters).
    Only use scrape_page if web_search snippets are insufficient.
    """
    if not _is_allowed_url(url):
        return (
            f"[Blocked: '{url}' not in allowed domains. "
            "Use web_search instead.]"
        )

    headers = {"User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        text  = soup.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        cleaned = "\n".join(lines)
        return cleaned[:_SCRAPE_CHAR_LIMIT] + (
            "\n[truncated]" if len(cleaned) > _SCRAPE_CHAR_LIMIT else ""
        )
    except requests.exceptions.Timeout:
        return f"[Timeout for {url} — use web_search instead]"
    except requests.exceptions.HTTPError as e:
        return f"[HTTP {e.response.status_code} — page may require login]"
    except Exception as e:
        return f"[Scrape error: {e}]"

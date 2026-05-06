"""
jd_utils.py — Lightweight JD helper functions for the Job Intelligence System.

Kept in a separate module so streamlit_app.py and main.py can import these
without triggering side-effects from the full main.py module load.
"""

import re


def extract_role_title(jd_text: str) -> str:
    """
    Extract job title from JD text before the parallel phase.
    Scans the first 6 non-empty lines (usually where the title appears).
    Returns the most likely title line, or 'this role' as fallback.
    No LLM call — pure heuristic, fast.
    """
    _skip = re.compile(
        r"^(location|salary|remote|hybrid|full[\s\-]?time|part[\s\-]?time|"
        r"posted|apply|about|company|job\s+id|requisition|ref[\s:]|date|"
        r"https?://|www\.|department|team|reports\s+to)",
        re.IGNORECASE,
    )
    lines = [l.strip() for l in jd_text.split("\n") if l.strip()]
    for line in lines[:6]:
        # Skip very short, very long, or obviously non-title lines
        if 4 < len(line) < 80 and not _skip.match(line):
            return line
    return "this role"


def extract_experience_level(jd_text: str) -> str:
    """
    Classify JD experience level: 'entry' | 'mid' | 'senior'.
    Used for seniority calibration of interview questions and JD database filtering.
    """
    text = jd_text.lower()

    senior_signals = [
        "senior", " lead ", "principal", "staff engineer", "architect",
        "5+ years", "6+ years", "7+ years", "8+ years", "10+ years",
        "5-8 years", "7-10 years", "five years", "six years",
    ]
    entry_signals = [
        "entry", "junior", " associate ", "graduate", "fresher",
        "0-2 years", "1-2 years", "0-1 year", "intern",
    ]

    if any(s in text for s in senior_signals):
        return "senior"
    if any(s in text for s in entry_signals):
        return "entry"
    return "mid"


def _clean_keyword(raw: str) -> str:
    """
    Normalise a single keyword line from LLM output.
    Handles:
      **Python** (explicitly required ...)  →  Python
      **NLP / Natural Language Processing** →  NLP / Natural Language Processing
      Docker / Microservices                →  Docker / Microservices
    """
    # Strip leading bullet / numbering — but NOT * (handled by bold regex below)
    raw = re.sub(r"^[\d]+[.)]\s*", "", raw.strip())
    raw = raw.lstrip("-• ").strip()
    # Remove markdown bold markers BEFORE stripping stray *
    raw = re.sub(r"\*\*([^*]+)\*\*", r"\1", raw)
    raw = re.sub(r"\*([^*]+)\*", r"\1", raw)
    # Strip any leftover lone asterisks (e.g. trailing ** that didn't pair)
    raw = raw.strip("* ").strip()
    # Strip trailing parenthetical explanation: "Python (required for X)" → "Python"
    raw = re.sub(r"\s*\([^)]{10,}\)\s*$", "", raw).strip()
    return raw




def preprocess_jd(text: str) -> str:
    """
    Normalize a raw JD before it enters the pipeline.

    Handles the most common real-world paste problems:
      - HTML tags / entities (copy from LinkedIn, Greenhouse, Lever, etc.)
      - Broken words from PDF copy (e.g. "experi-\nence")
      - Random mid-word line breaks (PDF column reflow)
      - Excessive blank lines
      - Non-JD noise: "Apply Now", page numbers, cookie notices, nav links
      - Windows line endings
      - Non-breaking spaces and other Unicode junk

    Does NOT restructure the content — that is the LLM's job.
    Only cleans formatting so the agent sees clean, readable text.
    """
    import re
    import html

    # 1. Windows line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 2. HTML entities (&amp; &lt; &nbsp; &#160; etc.)
    text = html.unescape(text)

    # 3. Strip HTML tags (handles <br>, <li>, <strong>, divs, etc.)
    #    Convert block-level tags to newlines first so we don't lose structure
    text = re.sub(r"<(br|p|div|li|tr|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)

    # 4. Non-breaking space and other Unicode whitespace → regular space
    text = re.sub(r"[\u00a0\u200b\u200c\u200d\ufeff\u2002-\u200a]", " ", text)

    # 5. Soft hyphens (U+00AD) — PDF copy artefact
    text = text.replace("\u00ad", "")

    # 6. PDF hyphenated line-break: "experi-\nence" → "experience"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    # 7. Mid-word line break (PDF reflow): a line ending mid-sentence with no
    #    punctuation and next line starts lowercase — join them.
    #    "We are look-\ning for" type artefacts are handled above.
    #    Handles: "responsible for build-\ning" → already fixed above.
    #    Now handle: "responsible\nfor building" (short dangling word)
    text = re.sub(r"([a-z,])\n([a-z])", r"\1 \2", text)

    # 8. Strip obvious non-JD noise lines
    _noise = re.compile(
        r"^("
        r"apply now|apply here|click to apply|easy apply|quick apply|"
        r"save job|share job|share this job|report job|"
        r"cookie|accept all cookies|privacy policy|terms of service|"
        r"sign in|log in|create account|register|"
        r"page \d+ of \d+|\d+ of \d+|"
        r"loading\.\.\.|please wait|"
        r"follow us|connect with us|"
        r"back to top|skip to|"
        r"https?://\S+"           # bare URLs (not in context)
        r")\s*$",
        re.IGNORECASE,
    )
    lines = text.split("\n")
    lines = [l for l in lines if not _noise.match(l.strip())]
    text = "\n".join(lines)

    # 9. Collapse 3+ consecutive blank lines → 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 10. Strip leading/trailing whitespace per line
    text = "\n".join(l.rstrip() for l in text.split("\n"))

    return text.strip()

def compute_ats_score(jd_analysis: str, tailored_cv: str) -> tuple:
    """
    Parse '## TOP JD KEYWORDS' from jd_analysis.
    Count how many appear in tailored_cv.
    Returns (found_count, total_count, found_keywords, missing_keywords).
    """
    # Patterns that indicate a line is a description/header, not a keyword
    _SKIP = re.compile(
        r"(most important|interviewers look|ats|from the jd|keywords.skills"
        r"|look for these|listed below|following keywords)",
        re.IGNORECASE,
    )

    keywords = []
    in_section = False
    for line in jd_analysis.split("\n"):
        if re.search(r"TOP\s+JD\s+KEYWORDS", line, re.IGNORECASE):
            in_section = True
            continue
        if in_section:
            if re.match(r"^#{1,3}\s", line):
                break
            cleaned = _clean_keyword(line)
            if not cleaned or len(cleaned) < 2:
                continue
            if _SKIP.search(cleaned):
                continue
            if len(cleaned) > 80:
                continue
            keywords.append(cleaned)

    if not keywords:
        return 0, 0, [], []

    cv_lower = tailored_cv.lower()
    found   = [kw for kw in keywords if kw.lower() in cv_lower]
    missing = [kw for kw in keywords if kw.lower() not in cv_lower]
    return len(found), len(keywords), found, missing

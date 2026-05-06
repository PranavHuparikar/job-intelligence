"""
sanitizer.py - Input validation and sanitisation for the Job Intelligence System.

Three layers of protection applied before any LLM call:
  1. Injection pattern removal  - regex-based, redacts prompt-injection attempts
  2. Length validation           - hard caps to prevent runaway token costs
  3. Authenticity validation     - fast heuristics + optional Haiku LLM check

Output validation:
  4. validate_pipeline_outputs   - sanity-checks results before showing to user

Security constraints:
  - CV text and JD text are never logged or stored by this module
  - The Haiku check only receives the first 1500 chars of each document
  - LLM validation failure is always non-fatal (defaults to allow-through)
"""

import json
import re
from typing import List, Tuple


# Length limits

CV_MIN_WORDS  =   50
CV_WARN_WORDS = 1600
CV_MAX_WORDS  = 2000

JD_MIN_WORDS  =   30
JD_MAX_WORDS  = 3000


# Injection pattern library

_PATTERNS: List[str] = [
    r"ignore\s+(?:all\s+)?(?:previous|prior|above|your)\s+instructions?",
    r"disregard\s+(?:all\s+)?(?:previous|prior|above|your)\s+instructions?",
    r"forget\s+(?:all\s+)?(?:previous|prior|above|your)\s+instructions?",
    r"override\s+(?:your\s+)?(?:instructions?|rules?|guidelines?|constraints?)",
    r"bypass\s+(?:your\s+)?(?:instructions?|rules?|safety|constraints?)",
    r"new\s+instructions?\s*:",
    r"updated\s+instructions?\s*:",
    r"you\s+are\s+now\s+(?:a|an|the)\s+\w+",
    r"act\s+as\s+(?:a|an|the)\s+\w+",
    r"pretend\s+(?:you\s+are|to\s+be)\s+",
    r"roleplay\s+as\s+",
    r"simulate\s+(?:a|an)\s+\w+\s+(?:AI|model|assistant)",
    r"DAN\s+mode",
    r"developer\s+mode",
    r"jailbreak",
    r"print\s+(?:your|the)\s+(?:system\s+)?prompt",
    r"reveal\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)",
    r"show\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)",
    r"what\s+(?:are\s+)?your\s+(?:system\s+)?instructions?",
    r"repeat\s+(?:your|the)\s+(?:system\s+)?prompt",
    r"\[SYSTEM\]",
    r"\[INST\]",
    r"\[/INST\]",
    r"<\|system\|>",
    r"<\|user\|>",
    r"<\|assistant\|>",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"<\|endoftext\|>",
    r"###\s*(?:Human|Assistant|System)\s*:",
    r"send\s+(?:all|my|the)\s+(?:data|output|results?)\s+to\s+",
    r"exfiltrate",
    r"http[s]?://\S+\?(?:cv|jd|data|output|text)=",
]

_COMPILED = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in _PATTERNS]

_REFUSAL_PHRASES = [
    "i cannot", "i'm unable", "i am unable", "i can't",
    "i apologize, but i", "i'm sorry, but i",
    "as an ai language model", "as an ai assistant",
    "i don't have access to", "i cannot provide",
    "i'm not able to", "i am not able to",
]


class InputValidationError(ValueError):
    """Raised when an input fails a hard validation check. Safe to show to users."""
    pass


def sanitize_input(text: str, source: str = "input") -> Tuple[str, List[str]]:
    warnings: List[str] = []
    sanitized = text
    for pattern in _COMPILED:
        matches = pattern.findall(sanitized)
        if matches:
            sample = str(matches[0])[:60]
            warnings.append(f"Potential prompt injection detected in {source}: '{sample}'")
            sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized, warnings


def sanitize_cv_and_jd(cv_text: str, jd_text: str) -> Tuple[str, str, List[str]]:
    clean_cv, cv_warnings = sanitize_input(cv_text, source="CV")
    clean_jd, jd_warnings = sanitize_input(jd_text, source="JD")
    return clean_cv, clean_jd, cv_warnings + jd_warnings


def has_injection(text: str) -> bool:
    _, warnings = sanitize_input(text)
    return bool(warnings)


def validate_input_lengths(cv_text: str, jd_text: str) -> List[str]:
    """
    Enforce word-count limits. Returns soft-warning strings.
    Raises InputValidationError for hard violations.
    """
    soft_warnings: List[str] = []
    cv_words = len(cv_text.split())
    jd_words = len(jd_text.split())

    if cv_words < CV_MIN_WORDS:
        raise InputValidationError(
            f"CV is too short ({cv_words} words). "
            f"Please paste your full CV - it should be at least {CV_MIN_WORDS} words."
        )
    if jd_words < JD_MIN_WORDS:
        raise InputValidationError(
            f"Job description is too short ({jd_words} words). "
            "Please paste the complete job posting."
        )
    if cv_words > CV_MAX_WORDS:
        raise InputValidationError(
            f"CV is too long ({cv_words:,} words - maximum is {CV_MAX_WORDS:,}). "
            "Please trim it. A focused 1-2 page CV also performs better with ATS systems."
        )
    if jd_words > JD_MAX_WORDS:
        raise InputValidationError(
            f"Job description is too long ({jd_words:,} words - maximum is {JD_MAX_WORDS:,}). "
            "Please paste only the core job posting content."
        )
    if CV_WARN_WORDS < cv_words <= CV_MAX_WORDS:
        soft_warnings.append(
            f"Your CV is {cv_words} words. For best ATS performance, aim for under {CV_WARN_WORDS}."
        )
    return soft_warnings


def validate_document_authenticity(
    cv_text: str,
    jd_text: str,
    use_llm: bool = True,
) -> Tuple[List[str], List[str]]:
    """
    Two-pass authenticity check.

    Pass 1 - fast structural heuristics (free, ~1 ms):
      Checks for CV signals (experience, skills, contact) and JD signals
      (role title, requirements, company context). Also catches code dumps,
      lorem ipsum, and base64 blobs.

    Pass 2 - Haiku LLM validation (~$0.001, ~2 s):
      Deeper check that documents are genuine and CV/JD domains match.
      Only runs if Pass 1 passes, use_llm=True, and ANTHROPIC_API_KEY is set.

    Returns (blocking_errors, warnings).
    blocking_errors non-empty means reject before pipeline starts.
    warnings non-empty means show to user but allow through.
    """
    errors: List[str] = []
    warnings: List[str] = []

    cv_lower = cv_text.lower()
    jd_lower = jd_text.lower()

    # Pass 1: structural heuristics
    cv_signals = [
        any(kw in cv_lower for kw in [
            "experience", "work history", "employment", "worked at",
            "worked for", "position held", "responsibilities",
        ]),
        any(kw in cv_lower for kw in [
            "skill", "proficient", "expertise", "technologies",
            "tools", "languages", "frameworks", "competencies",
        ]),
        any(kw in cv_lower for kw in [
            "education", "degree", "university", "college",
            "bachelor", "master", "phd", "diploma", "certification",
        ]),
        any(kw in cv_lower for kw in [
            "@", "email", "phone", "linkedin", "github",
            "contact", "mobile", "portfolio",
        ]),
    ]
    if sum(cv_signals) < 2:
        errors.append(
            "The CV doesn't look like a genuine resume. "
            "Please paste your actual CV with work experience, skills, and education."
        )

    jd_signals = [
        any(kw in jd_lower for kw in [
            "responsibilities", "duties", "you will", "role",
            "position", "job title", "about the role", "what you'll do",
        ]),
        any(kw in jd_lower for kw in [
            "requirements", "qualifications", "experience required",
            "must have", "looking for", "you have", "you bring",
            "desired skills", "what we need",
        ]),
        any(kw in jd_lower for kw in [
            "apply", "team", "company", "salary", "benefits",
            "location", "remote", "hybrid", "onsite", "about us",
            "who we are", "our team",
        ]),
    ]
    if sum(jd_signals) < 2:
        errors.append(
            "The job description doesn't look like a genuine job posting. "
            "Please paste the actual JD with role description and requirements."
        )

    cv_lines = [l for l in cv_text.splitlines() if l.strip()]
    if cv_lines:
        long_ratio = sum(1 for l in cv_lines if len(l) > 250) / len(cv_lines)
        if long_ratio > 0.5:
            errors.append(
                "The CV appears to contain code or a data dump rather than a resume. "
                "Please paste plain-text CV content."
            )

    if "lorem ipsum" in cv_lower or "lorem ipsum" in jd_lower:
        errors.append("Placeholder text (lorem ipsum) detected. Please use real content.")

    if re.search(r"[A-Za-z0-9+/]{200,}={0,2}", cv_text):
        errors.append(
            "The CV appears to contain encoded/binary data. "
            "Please paste plain-text CV content."
        )

    if errors:
        return errors, warnings

    # Pass 2: Haiku LLM check
    if not use_llm:
        return errors, warnings

    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        return errors, warnings

    try:
        import anthropic

        prompt = (
            "Validate these two documents. Respond ONLY with a JSON object - no other text.\n\n"
            "<cv>\n" + cv_text[:1500] + "\n</cv>\n\n"
            "<jd>\n" + jd_text[:1500] + "\n</jd>\n\n"
            "JSON fields:\n"
            '  "cv_ok": true if genuine professional CV/resume with real work history\n'
            '  "jd_ok": true if genuine job posting with a real role and requirements\n'
            '  "cv_issue": if cv_ok=false, one-line reason (max 80 chars), else ""\n'
            '  "jd_issue": if jd_ok=false, one-line reason (max 80 chars), else ""\n'
            '  "domain_match": true if CV background and JD role are broadly compatible\n'
            '  "domain_warning": if domain_match=false, one sentence on the mismatch, else ""\n\n'
            "Respond with ONLY the JSON object."
        )

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=250,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        json_match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            # LLM verdicts are advisory only — never block the pipeline.
            # Heuristics (Pass 1) are the gatekeeper; Haiku can misread
            # truncated inputs or non-standard CV layouts.
            if not result.get("cv_ok", True):
                issue = result.get("cv_issue", "")
                if issue:
                    warnings.append(f"CV note (advisory): {issue}")
            if not result.get("jd_ok", True):
                issue = result.get("jd_issue", "")
                if issue:
                    warnings.append(f"JD note (advisory): {issue}")
            if not result.get("domain_match", True):
                warn = result.get(
                    "domain_warning",
                    "The CV and JD appear to be from different professional fields."
                )
                warnings.append(f"Domain mismatch: {warn}")
    except Exception:
        pass  # LLM validation failure must NEVER block the pipeline

    return errors, warnings


def validate_pipeline_outputs(
    jd_analysis: str,
    company_research: str,
    tailored_cv: str,
) -> List[str]:
    """
    Sanity-check pipeline outputs before presenting to user.
    Returns a list of problem descriptions (empty = all OK).
    """
    issues: List[str] = []
    checks = [
        ("JD Analysis",      jd_analysis,      100),
        ("Company Research", company_research,   80),
        ("Tailored CV",      tailored_cv,       150),
    ]
    for label, text, min_words in checks:
        if not text or not text.strip():
            issues.append(f"{label}: output is empty")
            continue
        word_count = len(text.split())
        if word_count < min_words:
            issues.append(
                f"{label}: output is suspiciously short "
                f"({word_count} words, expected >= {min_words})"
            )
            continue
        start = text.lower()[:300]
        for phrase in _REFUSAL_PHRASES:
            if phrase in start:
                issues.append(
                    f"{label}: output may be an LLM refusal "
                    f"(starts with: '{text[:80].strip()}')"
                )
                break
    return issues

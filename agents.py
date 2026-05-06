"""
agents.py — CrewAI agent definitions for the Job Intelligence System.

Key design decisions:
- _fix_trailing_assistant: appends a "Continue." user turn so Claude 4.x
  never sees a conversation ending on an assistant message (400 Bad Request).
- _patched_completion / _patched_acompletion: adds (a) a proactive throttle
  (2 s minimum between API calls, thread-safe) to smooth token-per-minute
  pressure, and (b) exponential-backoff retry on 429 Rate Limit errors.
- Per-agent temperatures:
    JD Analyst         → 0.0  (deterministic extraction + arithmetic)
    Company Researcher → 0.0  (fact retrieval, no creativity needed)
    CV Tailor          → 0.3  (natural language rewriting, some variation)
    Interview Coach    → 0.1  (structured output, slight creativity for Q&A)
- max_iter: Company Researcher 12 (5 searches + retries + report); others 8.
"""

import langchain as _lc
if not hasattr(_lc, "verbose"):
    _lc.verbose = False
if not hasattr(_lc, "debug"):
    _lc.debug = False

import asyncio
import threading
import time

import litellm as _litellm


# ── Trailing-assistant fix ────────────────────────────────────────────────────

def _fix_trailing_assistant(messages: list) -> list:
    """
    Claude 4.x rejects conversations that end with an assistant message.
    CrewAI's ReAct executor appends the full thought+action+observation block
    as an assistant message before each follow-up call.  We must NOT strip it
    (that loses tool history and causes infinite loops).  Instead, append a
    minimal "Continue." user turn so Anthropic's validator passes.
    """
    if not messages:
        return messages
    last = messages[-1]
    role = last.get("role") if isinstance(last, dict) else getattr(last, "role", None)
    if role != "assistant":
        return messages
    return list(messages) + [{"role": "user", "content": "Continue."}]


# ── Proactive throttle ────────────────────────────────────────────────────────
# Tier-1 limit: 30,000 input tokens / minute.
# Spacing calls ≥ 2 s apart prevents burst spikes that trigger 429s.

_throttle_lock      = threading.Lock()
_last_call_time     = 0.0
_MIN_CALL_INTERVAL  = 2.0   # seconds between API calls


def _throttle():
    """
    Enforce minimum interval between API calls.
    sleep() is called OUTSIDE the lock so parallel threads are not serialised
    waiting for each other to finish sleeping.
    """
    global _last_call_time
    with _throttle_lock:
        now  = time.time()
        wait = _MIN_CALL_INTERVAL - (now - _last_call_time)
        # Advance _last_call_time now so the next thread calculates
        # from the correct baseline even while this thread is sleeping.
        _last_call_time = time.time() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)


# ── Rate-limit retry config ───────────────────────────────────────────────────

_RATE_LIMIT_MAX_RETRIES = 5
_RATE_LIMIT_BASE_WAIT   = 60   # one full token-window reset


# ── Patched completion (sync) ─────────────────────────────────────────────────

_orig_completion = _litellm.completion


def _patched_completion(model, messages=None, **kwargs):
    fixed = _fix_trailing_assistant(messages or [])
    _throttle()
    delay = _RATE_LIMIT_BASE_WAIT
    for attempt in range(_RATE_LIMIT_MAX_RETRIES):
        try:
            return _orig_completion(model, messages=fixed, **kwargs)
        except _litellm.RateLimitError:
            if attempt == _RATE_LIMIT_MAX_RETRIES - 1:
                raise
            print(
                f"\n  [Rate limit] 429 — waiting {delay}s "
                f"({attempt + 1}/{_RATE_LIMIT_MAX_RETRIES - 1})...\n"
            )
            time.sleep(delay)
            delay = min(delay * 2, 300)


# ── Patched completion (async) ────────────────────────────────────────────────

_orig_acompletion = _litellm.acompletion


async def _patched_acompletion(model, messages=None, **kwargs):
    fixed = _fix_trailing_assistant(messages or [])
    _throttle()
    delay = _RATE_LIMIT_BASE_WAIT
    for attempt in range(_RATE_LIMIT_MAX_RETRIES):
        try:
            return await _orig_acompletion(model, messages=fixed, **kwargs)
        except _litellm.RateLimitError:
            if attempt == _RATE_LIMIT_MAX_RETRIES - 1:
                raise
            print(
                f"\n  [Rate limit] 429 — waiting {delay}s "
                f"({attempt + 1}/{_RATE_LIMIT_MAX_RETRIES - 1})...\n"
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


_litellm.completion  = _patched_completion
_litellm.acompletion = _patched_acompletion

# ─────────────────────────────────────────────────────────────────────────────

from crewai import Agent, LLM
from tools import web_search, scrape_page


def build_llm(model: str = "claude-sonnet-4-6", temperature: float = 0.0) -> LLM:
    """Build a single LLM instance. Used by build_agents internally."""
    return LLM(
        model=f"anthropic/{model}",
        temperature=temperature,
        max_tokens=8096,   # Prevent output truncation on long interview prep / CV outputs
    )


def build_agents(model: str = "claude-sonnet-4-6") -> dict:
    """
    Instantiate all four agents with per-agent temperature settings.

    Temperature rationale:
      JD Analyst (0.0)         → pure extraction + arithmetic, no creativity
      Company Researcher (0.0) → factual synthesis from search results
      CV Tailor (0.3)          → rewriting needs natural variation, not robotic
      Interview Coach (0.1)    → structured sections, slight warmth in language
    """
    llm_zero    = build_llm(model, temperature=0.0)
    llm_tailor  = build_llm(model, temperature=0.3)
    # interview_coach generates 8+ sections in one pass — 16K gives full STAR story room
    llm_coach   = LLM(
        model=f"anthropic/{model}",
        temperature=0.1,
        max_tokens=16000,
    )

    _NO_HALLUCINATION_BACKSTORY = (
        "CORE RULE — non-negotiable: You NEVER fabricate, invent, or guess. "
        "If information is not in your inputs or search results, you write "
        "'Not confirmed.' You never write a URL you did not receive from a "
        "search tool. You never convert currencies or invent salary figures. "
        "A confident wrong answer is always worse than an honest 'Not confirmed.'"
    )

    # ── Agent 1: JD Analyst — no tools, deterministic ───────────────────────
    jd_analyst = Agent(
        role="Senior JD Analyst",
        goal=(
            "Deeply analyse a job description against the candidate's CV. "
            "Produce a structured fit report: step-by-step fit score calculation, "
            "top JD keywords, matching skills, skill gaps, and points to emphasise. "
            "Report ONLY what is explicitly stated — never infer or fill gaps."
        ),
        backstory=(
            "You are a seasoned technical recruiter with 10+ years of experience "
            "in AI/ML and software hiring. You read JDs with surgical precision — "
            "you know which keywords ATS systems weight heavily, which requirements "
            "are negotiable, and exactly how to map a candidate's experience to what "
            "an employer is looking for. You are rigorous with arithmetic: you always "
            "show your working before stating a score. "
            + _NO_HALLUCINATION_BACKSTORY
        ),
        llm=llm_zero,
        verbose=True,
        allow_delegation=False,
    )

    # ── Agent 2: Company Researcher — 5 searches, capped at 12 iters ─────────
    company_researcher = Agent(
        role="Company Intelligence Researcher",
        goal=(
            "Build a concise intelligence report on the target company: "
            "culture, salary ranges, interview process, employee sentiment, "
            "recent news, and red flags. "
            "Use EXACTLY 5 searches as specified — no more, no fewer."
        ),
        backstory=(
            "You are an investigative researcher who specialises in corporate "
            "due diligence for job seekers. You run 5 targeted searches, then "
            "synthesise everything into a single intelligence report. "
            "You are rigorous about attribution: every factual claim must be "
            "traceable to a search result. Consolidate ALL sources into a "
            "dedicated '## Sources Used' section — never scatter inline tags. "
            "You NEVER write a URL that was not returned by your search tool. "
            "You NEVER convert currencies — wrong-currency data is marked "
            "'Not applicable' not converted. "
            "You NEVER run more than 5 searches per task. "
            + _NO_HALLUCINATION_BACKSTORY
        ),
        llm=llm_zero,
        tools=[web_search, scrape_page],
        verbose=True,
        allow_delegation=False,
        max_iter=20,   # 5 mandatory + 2 fallback searches + 1 scrape + retries + synthesis
    )

    # ── Agent 3: CV Tailor — no tools, natural rewriting ─────────────────────
    cv_tailor = Agent(
        role="Expert CV Tailor",
        goal=(
            "Rewrite the candidate's CV to maximise its relevance for the "
            "target role — without fabricating any experience, skill, or achievement "
            "not explicitly present in the original CV."
        ),
        backstory=(
            "You are a professional CV writer who has helped 500+ engineers "
            "land roles at top-tier tech companies. You reframe, reorder, and "
            "reword to speak the exact language of the JD. ATS keyword "
            "optimisation is second nature to you. You have a strict rule: "
            "you never invent or embellish — every bullet you write must be "
            "traceable to the original CV. "
            + _NO_HALLUCINATION_BACKSTORY
        ),
        llm=llm_tailor,
        verbose=True,
        allow_delegation=False,
    )

    # ── Agent 4: Interview Coach — structured + warm ──────────────────────────
    interview_coach = Agent(
        role="Interview Preparation Coach",
        goal=(
            "Generate a comprehensive, personalised interview prep kit: "
            "scenario-based technical questions grounded in JD technologies, "
            "culture-specific behavioural questions, STAR stories anchored to "
            "actual CV content, location-appropriate salary negotiation script, "
            "red flags to probe, why-this-company talking point, "
            "30/60/90 day plan, and tiered questions to ask per interview round."
        ),
        backstory=(
            "You are a career coach and ex-hiring manager from the AI/tech industry "
            "with 12 years of experience. You know exactly what interviewers test for "
            "at different seniority levels. Your technical questions are always "
            "grounded in the specific technologies named in the JD — never generic "
            "textbook questions. Your STAR stories are grounded in the candidate's "
            "real experience — you never invent achievements. "
            "When no relevant CV experience exists for a story theme, you provide "
            "structured preparation guidance instead of fabricating a story. "
            "Your salary figures use only the currency and data from the company "
            "research — you never invent or convert figures. "
            + _NO_HALLUCINATION_BACKSTORY
        ),
        llm=llm_coach,
        tools=[],   # no web_search — single-pass generation; salary data arrives via company_researcher context
        verbose=True,
        allow_delegation=False,
        max_iter=12,  # raised from 8 — long output needs more generation steps
    )

    return {
        "jd_analyst":         jd_analyst,
        "company_researcher": company_researcher,
        "cv_tailor":          cv_tailor,
        "interview_coach":    interview_coach,
    }

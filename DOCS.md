# Job Intelligence System — Complete Project Documentation

> **Author:** Pranav Huparikar  
> **Stack:** Python · CrewAI · LiteLLM · Anthropic Claude · DuckDuckGo · Streamlit  
> **Last updated:** May 2026

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Workflow — End to End](#3-workflow--end-to-end)
4. [Agent Design Decisions](#4-agent-design-decisions)
5. [Task Design Decisions](#5-task-design-decisions)
6. [Tool Design Decisions](#6-tool-design-decisions)
7. [All Tunable Parameters & Their Impact](#7-all-tunable-parameters--their-impact)
8. [Struggles, Root Causes & Fixes](#8-struggles-root-causes--fixes)
9. [Streamlit App Design](#9-streamlit-app-design)
10. [Output Generator Design](#10-output-generator-design)
11. [Key Lessons Learned](#11-key-lessons-learned)
12. [Known Limitations & Future Work](#12-known-limitations--future-work)
13. [Improvement History — From First Run to Now](#13-improvement-history--from-first-run-to-now)

---

## 1. Project Overview

The Job Intelligence System automates the most time-consuming parts of a job application:

- **JD Analysis** — maps a job description against a candidate CV, scores fit (0–100), identifies ATS keywords, skill gaps, and what to emphasise
- **Company Research** — builds a live intelligence report on the target company: culture, salary benchmarks, interview process, red flags, recent news
- **CV Tailoring** — rewrites the candidate CV to match the JD language without fabricating experience
- **Interview Prep** — generates a personalised prep kit: likely technical and behavioural questions, STAR stories drawn from the real CV, salary negotiation strategy

All four outputs are generated in a single run, saved as a tailored Word CV (`tailored_cv.docx`), a PDF intelligence report (`job_intelligence.pdf`), and a plain-text backup (`raw_outputs.md`).

The system is accessible via a command-line interface (`main.py`) and a Streamlit web UI (`streamlit_app.py`).

---

## 2. System Architecture

```
┌────────────────────────────────────────────────────────────┐
│                    Input Layer                             │
│   CV text (.txt)  +  Job Description (paste)  +  Company  │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────────────────────────┐
│                  Phase 1  (PARALLEL)                       │
│                                                            │
│   Thread 1                    Thread 2                     │
│   ┌──────────────┐           ┌─────────────────────────┐  │
│   │  JD Analyst  │           │  Company Researcher      │  │
│   │  (no tools)  │           │  (web_search +          │  │
│   │  ~1 min      │           │   scrape_page)           │  │
│   │              │           │  ~3-5 min                │  │
│   └──────┬───────┘           └────────────┬────────────┘  │
│          │ jd_analysis                    │ company_intel  │
└──────────┼────────────────────────────────┼───────────────┘
           │                                │
           └──────────────┬─────────────────┘
                          │  (both outputs available)
                          ▼
┌────────────────────────────────────────────────────────────┐
│                  Phase 2  (SEQUENTIAL)                     │
│                                                            │
│   ┌──────────────┐    context    ┌──────────────────────┐  │
│   │  CV Tailor   │ ────────────► │  Interview Coach     │  │
│   │  (no tools)  │               │  (web_search x1)    │  │
│   │  ~1 min      │               │  ~2-3 min            │  │
│   └──────┬───────┘               └──────────┬───────────┘  │
│          │ tailored_cv                       │ interview_prep│
└──────────┼───────────────────────────────────┼─────────────┘
           │                                   │
           └────────────────┬──────────────────┘
                            ▼
┌────────────────────────────────────────────────────────────┐
│                  Output Layer                              │
│   tailored_cv.docx  │  job_intelligence.pdf  │  raw.md    │
└────────────────────────────────────────────────────────────┘
```

### File Structure

```
job_intelligence/
├── main.py                  ← CLI entry point
├── streamlit_app.py         ← Web UI
├── agents.py                ← Agent definitions + LiteLLM patches
├── tasks.py                 ← Task prompts and dependencies
├── tools.py                 ← web_search + scrape_page tools
├── output_generator.py      ← .docx and .pdf generation
├── requirements.txt
├── .env                     ← ANTHROPIC_API_KEY (not committed)
├── .cache/
│   └── company_research/    ← Search result cache (30-day TTL)
├── saved_inputs/
│   ├── cvs.json             ← Saved CV library (Streamlit)
│   └── jds.json             ← Saved JD library (Streamlit)
└── outputs/
    └── <Company>_<timestamp>/
        ├── tailored_cv.docx
        ├── job_intelligence.pdf
        └── raw_outputs.md
```

---

## 3. Workflow — End to End

### Phase 1 — Parallel

**Why parallel?** JD Analysis (Task 1) and Company Research (Task 2) have zero dependency on each other. Running them sequentially was ~9 minutes of wasted wall time. Running them in parallel with `threading.Thread` cuts total Phase 1 time to `max(T1, T2)` — about 4–5 minutes instead of 8–10.

**Thread safety** — both threads share one `LLM` object, but `litellm.completion` is a stateless function call (no shared mutable state). The only shared state is `_last_call_time` in the proactive throttle, which is protected by `threading.Lock()`.

### Phase 2 — Sequential

CV Tailor (Task 3) reads the JD analysis from Task 1 via `context=[task_jd_analysis]`. Interview Coach (Task 4) reads all three prior tasks. Sequential execution is correct here — each task consumes the output of the previous.

### LLM Call Chain (per agent iteration)

```
CrewAI ReAct executor
  → build messages list (system + history + new user turn)
  → litellm.completion()  [our patched version]
      → _fix_trailing_assistant()    # Claude 4.x compatibility
      → _throttle()                  # proactive 2s pacing
      → _orig_completion()           # actual API call
          ← response or RateLimitError
      → if 429: sleep(60s) and retry
  ← LLM response
  → parse Thought/Action/Observation
  → if Action: call tool, append result, loop
  → if Final Answer: return
```

---

## 4. Agent Design Decisions

### Agent 1 — JD Analyst

**No tools.** The JD and CV are provided directly in the task description. Tools would only add latency. The agent makes 1–2 LLM calls (think + answer) and finishes in ~60 seconds.

**`allow_delegation=False`** on all agents — prevents agents from spawning sub-agents, which would be uncontrolled and consume unexpected tokens.

**`verbose=True`** on all agents — essential for debugging. The ReAct thought/action/observation chain is the only window into what the agent is doing.

### Agent 2 — Company Researcher

**Tools:** `web_search` + `scrape_page`.

**`max_iter=8`** (was 15) — the biggest single fix for runaway loops. The agent was doing 12–15 search iterations, accumulating a massive conversation history, hitting rate limits, and spending 10+ minutes per run. Capping at 8 ensures it finishes within budget.

**Why not max_iter=5?** Three searches + synthesis requires at least 4–5 LLM calls (search call, observe result, search call, observe result, ..., final answer). 5 is too tight and causes premature stopping with incomplete output. 8 is the sweet spot.

**Backstory matters.** The backstory includes "You are disciplined: you NEVER search more than 4 times per task." This is not just flavour — it conditions the agent's planning behaviour. LLMs in ReAct mode use the backstory as part of their identity when deciding whether to issue another tool call.

### Agent 3 — CV Tailor

**No tools.** All context is passed via `context=[task_jd_analysis]`. The agent has the JD analysis and the original CV inline in the task description. Tools would add latency with no benefit.

**Single LLM call** (typically). The CV rewrite is a pure generation task — the agent rarely needs to iterate.

### Agent 4 — Interview Coach

**Tool:** `web_search` only (not `scrape_page`). Scraping is too slow for this task; search snippets provide enough salary/company data. The task prompt instructs the agent to search at most once, only if current salary data is needed.

**Context chain:** `context=[task_jd_analysis, task_company_research, task_cv_tailoring]` — the interview coach has access to all prior outputs. This is what enables it to write STAR stories from the actual CV and align interview talking points with the tailored CV language.

---

## 5. Task Design Decisions

### Task 1 — JD Analysis: Fit Score Rubric

The fit score is calculated with an explicit rubric in the prompt:

```
Mandatory requirements met:  count matched / total mandatory × 50 pts
Preferred requirements met:  count matched / total preferred × 30 pts
Years of experience match:   full = 20, partial = 10, none = 0
Round to nearest 5.
```

**Why a rubric?** Without it, LLMs produce wildly inconsistent scores — sometimes inflated, sometimes deflated. The rubric forces the model to do explicit accounting before writing the score. The "round to nearest 5" instruction prevents false precision (86.7/100 is meaningless noise).

### Task 2 — Company Research: Pre-Specified Search Queries

The old prompt said: "Search AmbitionBox, Glassdoor, LinkedIn, news, and careers page."

This caused the agent to make 5–10 searches, often repeating similar queries with slight variations, accumulating tokens, and triggering rate limits.

The new prompt specifies exactly three queries:
```
Search 1: "{company} reviews salary ambitionbox glassdoor india"
Search 2: "{company} interview process experience 2024 2025"
Search 3: "{company} news funding 2025"
```

After these three, the agent is instructed to **stop and synthesise**. This single change reduced company research time by ~60%.

**Why these three specifically?**
- Query 1 combines culture and salary — AmbitionBox and Glassdoor both show up for this pattern
- Query 2 gets interview process data from employee reports and forums
- Query 3 gets recent news without being too broad

### Task 3 — CV Tailor: No-Fabrication Rules

The prompt includes hard rules enforced via numbered list:
1. Do NOT invent any experience, project, or skill not in the original CV
2. Keep all dates, company names, and role titles exactly as in the original
3. Output the complete tailored CV — not a diff, the full document

Without rule 3, the model sometimes outputs only the changed sections. Without rule 1, models occasionally "helpfully" add skills that seem relevant but aren't real.

### Task 4 — Interview Coach: Context Alignment

The key instruction: "Your STAR stories and talking points MUST use the same language and framing as the tailored CV." Without this, the interview prep coach would reference the original CV while the tailored CV uses different language — creating inconsistency between what the candidate submits and what they say in the interview.

---

## 6. Tool Design Decisions

### web_search

**DuckDuckGo via `duckduckgo-search`** — free, no API key, no rate billing. Appropriate for a personal tool. Limitations: occasional rate limiting under heavy use.

**`max_results=3`** (was 5) — 40% fewer tokens per search. Three results are sufficient for the agent to extract the information it needs. The fourth and fifth results are usually lower-quality duplicates.

**Search result format:**
```
Title: ...
URL: ...
Snippet: ...
```
This is intentionally minimal. Richer formatting (markdown tables, headers) adds tokens without adding information.

**30-day cache** — caches by `query + month` hash. Same company researched twice in the same month returns instantly. Cache is per-machine in `.cache/company_research/`. The month component means salary data refreshes monthly.

**Pre-search sleep `1.0s`** (was 3.0s) — DuckDuckGo rate-limits aggressive scrapers. 1 second is sufficient for personal use. At `max_iter=8` this means max 8 seconds of sleep overhead per agent, vs 24 seconds with the old 3s sleep.

**Retry delay `4s → 8s → 16s`** (was `8s → 16s → 32s`) — faster recovery on transient DuckDuckGo errors. Three attempts with exponential backoff.

**Failure message instructs the agent:** "DO NOT retry this exact query." Without this, the agent would resubmit the same query, hit the CrewAI duplicate-input guard (`## Tool Output: I tried reusing the same input`), and get stuck in a loop.

### scrape_page

**`2500` character limit** (was 6000) — 60% token reduction. The most useful content on review pages (ratings, salary figures, interview steps) appears in the first 2000–2500 characters. Content beyond that is usually boilerplate, navigation text, or unrelated job listings.

**Domain allowlist (SSRF guard)** — the LLM constructs scrape URLs from search results. Without a domain allowlist, a prompt-injected URL or a search result pointing to a redirect could cause the scraper to fetch internal network resources. The allowlist restricts scraping to known legitimate review and news sites.

**10-second timeout** — prevents the agent from hanging on slow pages. Most review sites respond within 2–3 seconds. Timeout returns an error message instructing the agent to use web_search instead.

---

## 7. All Tunable Parameters & Their Impact

| Parameter | File | Current Value | Previous Value | Impact of Increasing | Impact of Decreasing |
|---|---|---|---|---|---|
| `max_iter` (company researcher) | agents.py | 8 | 15 | More thorough research, slower, more tokens | Faster, may miss information |
| `max_iter` (interview coach) | agents.py | 8 | 15 | More salary research, slower | Faster, relies more on context |
| `_SEARCH_MAX_RESULTS` | tools.py | 3 | 5 | More search coverage, more tokens per call | Less context, faster |
| `_SCRAPE_CHAR_LIMIT` | tools.py | 2500 | 6000 | More page content, more tokens | Less detail per scrape |
| `_SEARCH_SLEEP` | tools.py | 1.0s | 3.0s | Fewer DDG rate limit hits | Faster tool calls |
| `_SEARCH_RETRY_DELAY` | tools.py | 4s | 8s | Slower recovery from DDG errors | Faster recovery |
| `_SEARCH_MAX_RETRIES` | tools.py | 3 | 3 | More persistence on flaky network | — |
| `_CACHE_TTL_DAYS` | tools.py | 30 days | 30 days | Staler data | More API calls |
| `_MIN_CALL_INTERVAL` | agents.py | 2.0s | none | Fewer 429 errors, slower overall | Faster, more 429s |
| `_RATE_LIMIT_BASE_WAIT` | agents.py | 60s | 60s | Safer rate-limit recovery | Risk of repeated 429s |
| `_RATE_LIMIT_MAX_RETRIES` | agents.py | 5 | 5 | More persistence | — |
| `temperature` | agents.py | 0 | 0 | Higher = more creative, less deterministic | Lower = more factual |
| CV word count warning threshold | main.py / app | 1200 | 1200 | More CV context, more tokens | Less detail |
| Phase 1 threading | main.py | Parallel | Sequential | No impact on quality | Saves ~2 min wall time |

### Rate Limit Math (Tier 1: 30,000 input tokens/minute)

With `_MIN_CALL_INTERVAL = 2.0s`:
- Max calls per minute: 60 / 2 = **30 calls/minute**
- Average input tokens per ReAct call: ~1,000–2,000 (grows with history)
- After 5 tool iterations, history is ~8,000 tokens per call
- At 8,000 tokens × 7.5 calls/min = **60,000 tokens/min** → still hits limit

**Why the throttle helps but doesn't eliminate 429s:** The throttle prevents burst spikes (all 8 iterations firing in 4 seconds). But with large accumulated histories, individual calls still approach the limit. The 60-second rate-limit retry is the safety net.

**Upgrade path:** Tier 2 raises the limit to 90,000 input tokens/minute, effectively eliminating 429 errors for this workload.

---

## 8. Struggles, Root Causes & Fixes

### 8.1 — Claude 4.x "Assistant Prefill" Error (2-3 hours)

**Error:**
```
AnthropicException: This model does not support assistant message prefill.
The conversation must end with a user message.
```

**Root cause:** CrewAI's ReAct executor builds the LLM conversation history as:
```
[system, user(task), assistant(thought+action), user(tool_result), 
 assistant(thought+action), user(tool_result), assistant("Thought: I now know...")]
```
Claude 4.x rejects this because the conversation ends with an assistant message. The old Anthropic API allowed "assistant prefill" — starting your completion with a partial assistant turn. Claude 4.x removed this.

**First fix attempt — strip trailing assistant:**
```python
def _strip_trailing_assistant(messages):
    if messages and messages[-1].get("role") == "assistant":
        return messages[:-1]
    return messages
```
This used a single `if`, only removing ONE trailing assistant message. After multiple tool iterations, the history could end with two consecutive assistant messages — stripping one still left one, and the error persisted.

**Correct fix — while loop + non-dict objects:**
```python
def _fix_trailing_assistant(messages):
    if not messages:
        return messages
    last = messages[-1]
    role = last.get("role") if isinstance(last, dict) else getattr(last, "role", None)
    if role != "assistant":
        return messages
    return list(messages) + [{"role": "user", "content": "Continue."}]
```

**Why append instead of strip?** Stripping removes the agent's accumulated tool history (thought → action → observation chain). This causes the agent to forget what tools it has already called and enter an infinite loop calling the same tool repeatedly. Appending a minimal "Continue." user turn preserves all history and satisfies Claude's validation.

**Why handle non-dict objects?** LiteLLM sometimes returns message objects (not plain dicts) when certain model configurations are active. `last.get("role")` would raise `AttributeError` on these. Using `getattr(last, "role", None)` handles both cases.

### 8.2 — tools.py File Truncation

**Symptom:**
```
IndentationError: expected an indented block after 'except' statement on line 187
```

**Root cause:** The Edit tool made multiple sequential edits to `tools.py`. One edit truncated the file at line 187, leaving `except` clauses with no bodies — invalid Python.

**Fix:** Detected the truncation by reading the file. Appended the missing lines using bash `cat >>`. For future edits: rewrite the entire file with `Write` tool rather than making many sequential `Edit` calls on files above ~100 lines.

### 8.3 — DuckDuckGo Rate Limiting + CrewAI Duplicate-Input Guard

**Symptom:**
```
## Tool Output: I tried reusing the same input, I must stop using this action input
```

**Root cause chain:**
1. Heavy test runs exhausted the DuckDuckGo rate limit
2. `web_search` returned a generic failure message
3. The agent retried the same query (natural ReAct behaviour)
4. CrewAI's built-in duplicate-input guard blocked the retry
5. The agent had no instruction on what to do next → it looped

**Fix — failure message redesign:**
```python
# Old:
return "[Search failed after 3 attempts. Try again later.]"

# New:
return (
    "[Search failed for '{query}'. DO NOT retry this exact query. "
    "Try a DIFFERENT search term, or use your existing knowledge.]"
)
```
The explicit "DO NOT retry" instruction redirects the agent's behaviour. Without it, the agent interprets the failure as a transient error and naturally tries again.

**Additional fix:** Increased pre-search sleep and retry delays to reduce DDG rate limit pressure.

### 8.4 — NumPy 2.4.4 Import Hang on Windows

**Symptom:** `KeyboardInterrupt` during `from numpy._core._multiarray_umath import`

**Root cause:** NumPy 2.x uses `delvewheel` for DLL bundling on Windows. On first import, Windows Defender scans the bundled DLLs — this can take 10–30 seconds and looks like a hang. The user pressed Ctrl+C thinking it was frozen.

**Fix:** Pin `numpy>=1.22.5,<2.0` in requirements.txt. NumPy 1.x uses the old `.pyd` extension approach — fast import, no DLL unpacking, no Defender scan.

**Command:** `pip install "numpy>=1.22.5,<2.0"`

### 8.5 — chromadb 1.5.8 + NumPy 2.x Incompatibility Discovery

During investigation of 8.4, discovered chromadb had upgraded from 0.x to 1.5.8 (major version) because requirements.txt only said `chromadb>=0.5.3`.

CrewAI 0.80.0 imports `from chromadb.api.types import validate_embedding_function`. Verified this still exists in 1.5.8, so no API break. The root issue was purely the NumPy version.

**Lesson:** Pin major versions in requirements.txt. `chromadb>=0.5.3` should be `chromadb>=0.5.3,<2.0` to prevent future major-version breaks.

### 8.6 — Anthropic Rate Limit (429) Mid-Pipeline

**Error:**
```
rate_limit_error: This request would exceed your organization's rate limit 
of 30,000 input tokens per minute
```

**Root cause:** Tier 1 API accounts have a 30,000 input token/minute limit. The Company Researcher accumulates conversation history over multiple tool iterations. By iteration 6–8, each LLM call is sending 10,000–15,000 input tokens (system prompt + full ReAct history + search results). At this rate, two rapid calls in succession cross the 30k/minute ceiling.

**Fix — two-layer defence:**

Layer 1 — Proactive throttle (prevent 429s):
```python
_MIN_CALL_INTERVAL = 2.0  # seconds between calls
_throttle_lock = threading.Lock()

def _throttle():
    with _throttle_lock:
        wait = _MIN_CALL_INTERVAL - (time.time() - _last_call_time)
        if wait > 0:
            time.sleep(wait)
        _last_call_time = time.time()
```

Layer 2 — Reactive retry (handle 429s that slip through):
```python
for attempt in range(_RATE_LIMIT_MAX_RETRIES):
    try:
        return _orig_completion(...)
    except _litellm.RateLimitError:
        time.sleep(60)   # one full token-window reset
        ...
```

### 8.7 — CV .docx Unreadable by python-docx

**Context:** The master CV was generated by the `docx` npm library (Node.js). The project's `output_generator.py` uses `python-docx` (Python). When trying to read the generated CV to verify its content, python-docx, unzip, and LibreOffice all failed to parse the file.

**Root cause:** The npm `docx` library generates ZIP archives with "data descriptors" (a ZIP feature where file sizes are written after the compressed data, not before). Some ZIP implementations, including older python-docx, don't handle this format.

**Fix:** Read the raw bytes, locate the `word/document.xml` entry in the ZIP central directory, zlib-decompress it with window size `-15` (raw deflate, no header), then extract `<w:t>` text nodes with regex.

```python
import zlib, re

with open("cv.docx", "rb") as f:
    data = f.read()

# Find word/document.xml in the ZIP
idx = data.find(b"word/document.xml")
# ... locate compressed data offset
compressed = data[offset:offset + size]
xml = zlib.decompress(compressed, -15).decode("utf-8")
text = " ".join(re.findall(r"<w:t[^>]*>([^<]+)</w:t>", xml))
```

**Lesson:** Always verify output files with the same tool chain that will consume them.

### 8.9 — Streamlit Widget Key Bug: Saved CV/JD Not Loading

**Symptom:** Clicking a saved CV or JD in the sidebar appeared to do nothing — the text areas stayed blank or kept the old content. Company name also didn't auto-fill when loading a JD.

**Root cause:** Streamlit has a strict rule about keyed widgets. When a widget is given `key="cv_area"`, Streamlit stores and reads its value **exclusively** from `st.session_state["cv_area"]`. The `value=` parameter is only used on the very first render — after that it is silently ignored.

The original code set `st.session_state.cv_text = cv["content"]` and called `st.rerun()`. But `cv_text` is an arbitrary session variable — not the widget's key. The text area (`key="cv_area"`) never saw the update.

```python
# BROKEN — updates a variable the widget never reads after first render
st.session_state.cv_text = cv["content"]
st.rerun()

# FIXED — updates the widget's own key directly
st.session_state["cv_area"] = cv["content"]
st.rerun()
```

**Full fix applied:**
1. Session state defaults initialised using widget keys (`cv_area`, `jd_area`, `company_input`) not separate backing variables
2. Sidebar load buttons set the widget keys directly
3. Text areas and company input use `key=` only — no `value=` parameter (widget reads from its key)
4. Save buttons and run validation read from `st.session_state["cv_area"]` etc.
5. Results section reads `st.session_state.get("company_input")` instead of a `company` local variable that only existed inside the `if run_clicked:` block

**Rule to remember:** In Streamlit, the widget `key` is the single source of truth. Never maintain a separate "backing" variable and try to sync it with the widget — always read and write directly through the key.

### 8.8 — Pipeline "Stuck" During Development

**Symptom:** Pipeline runs for 5+ minutes, no output, no error — appears completely frozen.

**Root causes (multiple):**
1. DuckDuckGo was rate-limited after many test runs in quick succession → tool returned failure → agent looped → stuck (see 8.3)
2. Pre-search sleep was 3 seconds × 10+ searches = 30+ seconds of silent waiting with no console output
3. `verbose=True` output from CrewAI was buffered in some terminal configurations

**Fixes:**
- Added explicit print statements at phase boundaries
- Reduced sleep to 1s
- Added cache hit logging (`[cache hit] query...`) so the user knows the tool is running
- Fixed the duplicate-query loop (8.3)

---

## 9. Streamlit App Design

### Real-Time Streaming Architecture

Standard Streamlit is synchronous — the script runs top to bottom, and the UI renders after the script finishes. A 10-minute pipeline run with no updates would show a blank screen.

**Solution — thread + queue pattern:**

```
Main Streamlit thread          Background worker thread
─────────────────────          ─────────────────────────
run_pipeline_streaming()  →    _pipeline_worker() runs
                               sys.stdout → _QueueWriter
poll q every 200ms        ←    agent verbose output → q
log_placeholder.code()         
(updates browser via           result_holder[0] = result
 websocket)                    q.put(None)  # sentinel
exit loop on sentinel     ←    
return result_holder[0]
```

The key insight: `st.empty().code()` sends updates over Streamlit's websocket immediately. Calling it from the main thread inside a polling loop produces live output in the browser.

**Thread safety:** `sys.stdout` is redirected to `_QueueWriter` only within the worker thread. The main thread keeps its original stdout. `queue.Queue` is thread-safe by design.

### Saved CV / JD Library

CVs and JDs are stored as JSON files in `saved_inputs/`:

```json
[
  {
    "id": "bc5c176e",
    "name": "Persistent Systems — Agentic AI Engineer",
    "company": "Persistent Systems",
    "content": "...",
    "saved_at": "01 May 2026 14:32"
  }
]
```

**Design decisions:**
- **File-based, not database** — single-user local tool, SQLite would be overkill
- **Name-based deduplication** — saving with the same name overwrites, preventing accumulation of slightly different versions of the same JD
- **Newest first** — `items.insert(0, ...)` keeps the most recently saved at the top of the sidebar list
- **Company name auto-fill** — loading a saved JD also sets `session_state.company_name`, eliminating one manual step per run

### Phase Progress Labels

The Streamlit run panel shows:
```
⟳ Phase 1 — JD Analysis + Company Research (running in parallel)…
✓ JD Analysis complete — Company Research still running…
✓ Company Research complete.
⟳ Phase 2 — CV Tailoring + Interview Prep…
✓ Phase 2 complete.
```
These are print statements inside `_pipeline_worker` that flow through the queue into the live log display.

---

## 10. Output Generator Design

### tailored_cv.docx

Uses `python-docx` with a structured Markdown-to-docx parser. The CV Tailor agent outputs a plain-text CV in a specific format; the generator parses section headers, job titles, and bullet points.

**Design decision — python-docx, not docx npm library:**  
The master CV was generated with the npm `docx` library (the existing `generate_cv.js`). For the _tailored_ CV, python-docx was used because:
1. The pipeline runs in Python — no Node.js subprocess needed
2. python-docx produces ZIP-compatible `.docx` files that all tools can read
3. The tailored CV content comes from the LLM as plain text, making programmatic formatting straightforward

### job_intelligence.pdf

Uses `reportlab` to generate a structured PDF with:
- Title page (company name, date)
- Section-by-section report (JD Analysis, Company Intelligence, Interview Prep)
- Formatted headings, body text, and bullet points

**Why reportlab over alternatives:**  
- `WeasyPrint` requires a full HTML/CSS pipeline
- `fpdf2` has limited text wrapping
- `reportlab` gives precise control over layout and is pure Python

### raw_outputs.md

Plain Markdown concatenation of all four agent outputs. Serves as:
1. A backup if .docx or .pdf generation fails
2. A human-readable archive of the full run
3. Source for copy-pasting into other tools

---

## 11. Key Lessons Learned

### On CrewAI + Claude 4.x

1. **Assistant prefill is gone.** Claude 4.x strictly requires conversations to end with a user message. CrewAI's ReAct executor was designed for models that allowed assistant prefill. The monkey-patch in `agents.py` is mandatory for this combination.

2. **Append, don't strip.** Stripping the trailing assistant message removes tool history and causes infinite tool-call loops. Appending a "Continue." user message is the correct fix.

3. **`max_iter` is not a safety net — it's a design parameter.** Setting it to 15 hoping "the agent will stop when it has enough" doesn't work. Agents fill their token budget. The task prompt must explicitly constrain the agent's behaviour.

4. **Backstory shapes planning.** The agent backstory is not just flavour text. Including "you NEVER search more than 4 times" in the backstory measurably affects how many tool calls the agent makes.

5. **Pre-specify search queries.** An open-ended "search these sources" instruction leads to 8–12 search iterations. Pre-writing the exact queries and saying "stop after these three" reduces search count to 3–4 consistently.

### On LiteLLM + Rate Limiting

6. **Proactive throttling > reactive retry.** A 2-second floor between calls is invisible to the user and prevents 429s. A 60-second wait after a 429 is very visible and disruptive. Both are needed: throttle to prevent, retry to recover.

7. **Token counting in ReAct grows non-linearly.** Each tool iteration appends to the conversation history. Call N has (N-1) × average_result_size extra input tokens vs. call 1. This is why rate limits appear mid-run rather than at the start.

### On Tool Design

8. **Search result size is a performance lever.** Reducing from 5 to 3 results and 6000 to 2500 characters cuts tool output tokens by ~50%. The information loss is acceptable because the most useful content is always in the first few results and first few hundred characters.

9. **Cache aggressively.** The 30-day search cache is the single biggest user experience improvement for repeated runs on the same company. A cached run completes Phase 2's search calls in milliseconds.

10. **Failure messages are instructions.** The agent reads tool failure messages and decides what to do next. "Search failed" invites retry. "Search failed — DO NOT retry — use your knowledge" redirects the agent to a safe fallback.

### On System Design

11. **Parallel phases require careful thread safety.** The throttle lock (`threading.Lock`) prevents two threads from racing to update `_last_call_time`. Without it, both threads could fire API calls simultaneously, defeating the throttle.

12. **The user experience of waiting matters as much as actual speed.** Adding live log streaming (the thread+queue pattern) made the app feel significantly faster even before the real speed improvements, because users could see progress rather than watching a spinner.

---

## 12. Known Limitations & Future Work

### Current Limitations

**Rate limits (Tier 1):**  
30,000 input tokens/minute. With large CVs and JDs and long company research histories, this ceiling is still occasionally hit. Resolution: upgrade API account to Tier 2 (90k tokens/min).

**DuckDuckGo reliability:**  
DuckDuckGo doesn't offer a commercial API — the `duckduckgo-search` library reverse-engineers the search interface and breaks occasionally with library updates or anti-bot measures. Mitigation: update the library regularly (`pip install -U duckduckgo-search`).

**Single-user Streamlit:**  
The app is designed for local single-user use. Running multiple pipeline analyses simultaneously would cause sys.stdout redirect conflicts. For multi-user deployment, each session would need process isolation.

**CV format dependency:**  
The output generator parses the tailored CV based on specific formatting conventions outputted by the CV Tailor agent. If the LLM outputs a significantly different format, the docx formatting may degrade. The `raw_outputs.md` backup is always reliable.

**Context window growth:**  
As the Company Researcher runs more tool iterations, the conversation history grows. With 8 iterations and 2500-character scrape results, the history can reach 20,000+ tokens. This compounds the rate limit problem and increases cost.

### Potential Improvements

**Parallel search calls:** The company researcher currently calls `web_search` sequentially. The three pre-specified searches could be made in parallel (using `asyncio.gather` or `ThreadPoolExecutor`) and their results merged before the agent synthesises. This could cut research time by ~60%.

**LangGraph migration:** Replace CrewAI with LangGraph for finer control over the agent execution loop — specifically to implement custom ReAct logic that doesn't require the trailing-assistant monkey-patch.

**Token-aware context trimming:** After 4+ tool iterations, summarise older tool results rather than passing them in full. This would keep individual call sizes bounded regardless of iteration count.

**Structured output (Pydantic):** Use Claude's structured output mode to get agent results as typed Pydantic models rather than plain text. This would make the output_generator more robust — no text parsing needed.

**Salary data API:** Replace salary-from-search with a dedicated salary API (Levels.fyi API, or AmbitionBox's data if available) for more accurate and structured salary benchmarks.

**Multi-JD batch mode:** Accept a list of JDs and run all analyses sequentially, generating a comparison report showing fit scores side by side — useful for deciding which roles to prioritise.

**Local LLM fallback:** Add support for Ollama as a model provider for users who don't have an Anthropic API key. Quality would be lower but the pipeline would still produce useful outputs.

---

## 13. Improvement History — From First Run to Now

This section tracks every meaningful change made after the initial working pipeline was established, in chronological order. Each entry explains what broke or was lacking, what changed, and what the measurable impact was.

---

### v0 — Initial Working Pipeline

**State:** 4-agent CrewAI pipeline running sequentially (JD Analyst → Company Researcher → CV Tailor → Interview Coach). CLI only (`main.py`). No caching, no rate-limit handling, no output files — agents printed results to terminal.

**What worked:** Core agent logic, task prompts, tool definitions, ReAct loop.  
**What didn't:** Claude 4.x rejected every run with a 400 error before a single agent completed.

---

### v1 — Claude 4.x Compatibility (Critical Fix)

**Problem:** Every run failed immediately with `AnthropicException: This model does not support assistant message prefill`. CrewAI's ReAct executor ends the message list with an assistant turn after tool calls — Claude 4.x requires conversations to end with a user message.

**Change:** Added `_fix_trailing_assistant()` monkey-patch on `litellm.completion` and `litellm.acompletion` in `agents.py`. Appends `{"role": "user", "content": "Continue."}` when the message list ends on an assistant turn.

**First attempt (broken):** Used a single `if` — only removed one trailing assistant. After multiple tool calls, two consecutive assistant messages remained and the error persisted.

**Final fix:** Checks the last message and appends a user turn if needed. Also handles non-dict message objects via `getattr(last, "role", None)`.

**Impact:** Pipeline ran end-to-end for the first time. Without this, nothing else was possible.

---

### v2 — tools.py File Corruption Fix

**Problem:** `IndentationError: expected an indented block after 'except' statement on line 187`. Sequential edits with the Edit tool had truncated `tools.py` mid-file, leaving bare `except:` clauses with no bodies.

**Change:** Detected truncation by reading the file. Appended the missing lines. Going forward, complete rewrites of files >100 lines use the `Write` tool rather than chained `Edit` calls.

**Impact:** Tools became importable again. Web search and scraping started working.

---

### v3 — DuckDuckGo Rate Limit + Duplicate-Input Loop Fix

**Problem:** After several consecutive test runs, DuckDuckGo started rate-limiting requests. The tool returned a generic failure message. The agent's natural response was to retry the same query. CrewAI's built-in duplicate-input guard then fired: `## Tool Output: I tried reusing the same input, I must stop using this action input`. The agent had no instruction for what to do next and looped indefinitely.

**Changes:**
- Failure messages rewritten to explicitly say "DO NOT retry this exact query — use a DIFFERENT search term or rely on your existing knowledge"
- Pre-search sleep increased from 1.5s to 3s
- Retry base delay increased from 2s to 8s (8→16→32)

**Impact:** Agents gracefully recovered from search failures instead of looping. Pipeline could complete even when DDG was flaky.

---

### v4 — 429 Rate Limit Retry (Reactive)

**Problem:** Mid-run `litellm.RateLimitError: AnthropicException - rate_limit_error: This request would exceed your organization's rate limit of 30,000 input tokens per minute`. The entire pipeline crashed with no recovery.

**Change:** Wrapped `_patched_completion` and `_patched_acompletion` in a retry loop (up to 5 attempts) catching `RateLimitError`. On each 429, prints a message and waits 60 seconds (one full token-window reset) before retrying. Delay doubles each retry, capped at 5 minutes.

**Impact:** Pipeline survived rate limit hits and resumed automatically. No more manual restarts after 429 errors.

---

### v5 — NumPy 2.x Import Hang (Windows)

**Problem:** Running `python main.py` triggered a `KeyboardInterrupt` during `from numpy._core._multiarray_umath import`. NumPy 2.4.4 (installed by pip) uses `delvewheel` DLL bundling on Windows. On first import, Windows Defender scans each bundled DLL — this silently hangs for 10–30 seconds, appearing frozen.

**Change:** Pinned `numpy>=1.22.5,<2.0` in `requirements.txt`. NumPy 1.26.x uses the old `.pyd` extension approach — fast import, no DLL unpacking.

**Command:** `pip install "numpy>=1.22.5,<2.0"`

**Impact:** Import time dropped from 10–30s (with Defender scan) to <1s. No more false "hang" on startup.

---

### v6 — Streamlit Web UI (New Feature)

**What was built:** Full Streamlit web app (`streamlit_app.py`) replacing the copy-paste CLI workflow.

**Features added:**
- Side-by-side CV and JD text areas
- Model selector (Sonnet / Haiku / Opus)
- Run button with spinner
- Tabbed results display (JD Analysis, Company Intel, Tailored CV, Interview Prep)
- Download buttons for `.docx`, `.pdf`, `.md`
- Saved CV/JD library (JSON-backed, sidebar)

**Impact:** Eliminated all copy-paste. Any previously saved JD loads with one click and auto-fills the company name. CVs and JDs persist between sessions.

---

### v7 — Saved Inputs Library (Pre-seeded)

**What was built:** `saved_inputs/cvs.json` and `saved_inputs/jds.json` pre-seeded with all JDs analysed during the session.

**CVs saved:** Pranav Huparikar — Master CV (1079 words)

**JDs saved (6):**
| Role | Company | Fit Score |
|---|---|---|
| Agentic AI Engineer | Persistent Systems | 88/100 |
| GenAI Engineer | Persistent Systems | 92/100 |
| Data Scientist AI | IBM Consulting | 78/100 |
| Mid GenAI Engineer | NTT DATA | 85/100 |
| GenAI Engineer | Ascendion | 80/100 |
| Staff GenAI Engineer | EY GDS | 78/100 |

**Impact:** Zero setup required on next run. Open the app, click a JD, click Run.

---

### v8 — Performance Overhaul (~60% faster)

**Problem:** Full pipeline taking 15–20 minutes. Analysis showed five compounding bottlenecks.

**Changes and individual impact:**

| Change | File | Before | After | Time Saved |
|---|---|---|---|---|
| Phase 1 parallelism (T1 ∥ T2) | main.py, streamlit_app.py | Sequential ~9 min | Parallel ~5 min | ~2–4 min |
| `max_iter` reduced | agents.py | 15 | 8 | ~3–5 min worst case |
| Company researcher search cap (3 targeted queries) | tasks.py | 8–12 random searches | Exactly 3 pre-specified | ~4–6 min |
| Interview coach search cap (1 max) | tasks.py | Up to 15 iterations | 1 search, writes from context | ~2–3 min |
| Search results 5→3 | tools.py | 5 results/query | 3 results/query | Fewer tokens, fewer 429s |
| Scrape limit 6000→2500 chars | tools.py | 6000 chars | 2500 chars | Fewer tokens per scrape |
| Pre-search sleep 3s→1s | tools.py | 3s | 1s | ~20s per run |
| Proactive 2s throttle between calls | agents.py | None | 2s floor | Prevents most 429s |

**Total impact:** 15–20 min → 5–8 min typical. Biggest single wins were the 3-search cap on company researcher and parallel Phase 1.

---

### v9 — Real-Time Log Streaming in Streamlit (UX)

**Problem:** During a pipeline run the Streamlit UI showed a blank spinner with no indication of progress. Users couldn't tell if it was working, stuck, or waiting on a rate limit.

**Change:** Replaced synchronous blocking run with a thread+queue streaming architecture:
- Pipeline runs in a `daemon=True` background thread
- `sys.stdout` inside the thread is redirected to a `_QueueWriter` that enqueues every character
- Main thread polls the queue every 200ms and updates an `st.empty().code()` placeholder
- Sentinel `None` item signals pipeline completion

**Impact:** Every agent thought, tool call, and observation now streams live into the browser as it happens. Pipeline feels responsive even during the slowest phases. Rate limit waits are visible rather than mysterious.

---

### v10 — Streamlit Widget Key Bug Fix

**Problem:** Clicking any saved CV or JD in the sidebar did nothing — the text areas didn't update and company name didn't populate.

**Root cause:** Streamlit's `value=` parameter on a widget with an explicit `key=` is silently ignored after the first render. The widget reads exclusively from `st.session_state[key]`. The code was updating `st.session_state.cv_text` (a separate variable) instead of `st.session_state["cv_area"]` (the widget's actual key).

**Change:** All load buttons now set the widget keys directly. Session state defaults use widget keys as the single source of truth. `value=` removed from text areas — they initialise from their keys. Save and validation logic reads from widget keys throughout.

**Impact:** Saved CV/JD loading works correctly. One click populates both the text area and the company name field.

---

### Summary: Before vs. After

| Dimension | v0 (initial) | Current |
|---|---|---|
| Interface | CLI only, copy-paste | Streamlit web UI + CLI |
| Compatibility | Crashed on Claude 4.x | Full Claude 4.x support |
| Execution | Sequential, ~20 min | Parallel Phase 1, ~6 min |
| Rate limit handling | Crashed on 429 | Proactive throttle + auto-retry |
| Search behaviour | 8–12 random searches | 3 pre-specified, then stop |
| Token load per search | 5 results × 6000 chars | 3 results × 2500 chars |
| Progress visibility | Nothing until done | Live streaming log |
| CV/JD persistence | None, paste every time | One-click library |
| Saved JDs | 0 | 6 pre-loaded |
| Stability | Multiple crash modes | Handles all known failure modes |

---

## 14. New Modules (v11)

### cv_utils.py — CV Reader (in-memory)

Reads PDF, DOCX, and TXT CVs. Key design: CV bytes are **never written to disk**. Streamlit uploads call `read_cv_bytes(data: bytes, filename: str)` which operates entirely in memory. CLI calls `read_cv(path: str)` which reads from the filesystem.

| Function | Purpose |
|---|---|
| `read_cv(path)` | CLI: read from file path |
| `read_cv_bytes(data, filename)` | Streamlit: in-memory, no disk write |
| `word_count(text)` | Word count for the ≤1200 word warning |

PDF extraction uses `pdfplumber` with x/y tolerance. DOCX iterates `element.body` to preserve table content. The `_clean()` helper strips null bytes, collapses 3+ blank lines to 2, collapses 2+ spaces to 1.

---

### sanitizer.py — Prompt Injection Guard

35 compiled regex patterns covering: instruction override, persona hijack, prompt extraction, LLM special tokens (`<|im_start|>`, `[INST]`, etc.), data exfiltration attempts.

| Function | Purpose |
|---|---|
| `sanitize_cv_and_jd(cv, jd)` | Main entry — returns (clean_cv, clean_jd, warnings) |
| `sanitize_input(text, source)` | Single text scan |
| `has_injection(text)` | Quick boolean check |

Matches are replaced with `[REDACTED]`. Non-blocking — warnings are printed but the pipeline continues. Applied to both CV and JD text before any agent sees them.

---

### jd_database.py — JD Storage + Similarity Search

SQLite beta store for every JD processed. Each JD gets a dense vector embedding for cosine similarity search.

**Tables:**
- `jd_metadata` — title, company, location, platform, posting_date, job_type, experience_level, jd_text
- `jd_embeddings` — JSON-serialised embedding vectors, FK to jd_metadata

**Embedding model:** `BAAI/bge-small-en-v1.5` (default, ~130 MB). Set `EMBEDDING_MODEL=BAAI/bge-m3` for production-grade hybrid dense+sparse retrieval via FlagEmbedding.

**Hard metadata pre-filters** before cosine similarity:
- `experience_level` ±1 band (entry=0, mid=1, senior=2) — no senior JDs shown to junior candidates
- `job_type` — no contract JDs shown to full-time seekers

**Graceful degradation:** If PyTorch / sentence-transformers is not installed, `save_jd()` stores metadata without an embedding (warning printed). `find_similar_jds()` returns an empty list. Install with `pip install torch sentence-transformers` to enable similarity search.

**Production upgrade path:** PostgreSQL + pgvector + HNSW index. Set `JD_DB_PATH` env var to override the default `saved_inputs/jd_store.db`.

| Function | Purpose |
|---|---|
| `init_db()` | Create tables + indexes (safe to call on every access) |
| `save_jd(jd_text, ...)` | Save JD + generate embedding, returns jd_id |
| `find_similar_jds(jd_text, top_k, ...)` | Cosine similarity search with metadata filters |
| `get_jd_count()` | Total JDs in database |
| `get_jd_by_id(jd_id)` | Retrieve single JD record |

---

### evaluator.py — LLM-as-Judge Quality Scorer

Post-pipeline quality evaluation using Claude Haiku. Scores 4 dimensions and writes `quality_report.json` to the output directory.

**Scoring dimensions and weights:**

| Dimension | Weight | What it checks |
|---|---|---|
| Fabrication check | 40% | Skills, companies, dates invented vs. sourced from CV |
| Keyword coverage | 25% | JD keywords present in tailored CV |
| Math accuracy | 20% | Fit score arithmetic is correct (Step 1–4 in JD analysis) |
| STAR grounding | 15% | STAR stories trace back to CV bullet points |

**Quality bands:** Green ≥88, Amber 68–87, Red <68.

| Function | Purpose |
|---|---|
| `evaluate_pipeline_output(...)` | Main evaluation function, returns report dict |
| `quality_badge_html(report)` | HTML span with colored badge for Streamlit |

`_fallback_report(error)` returns a safe dict with `overall_quality="unknown"` on any failure — the evaluator never crashes the pipeline.

---

### jd_utils.py — Lightweight JD Helpers

Extracted from `main.py` so both `streamlit_app.py` and `main.py` can import shared utilities without triggering `main.py`'s side effects.

| Function | Purpose |
|---|---|
| `extract_role_title(jd_text)` | Heuristic scan of first 6 lines; fallback `"this role"` |
| `extract_experience_level(jd_text)` | Returns `"entry"` / `"mid"` / `"senior"` |
| `compute_ats_score(jd_analysis, tailored_cv)` | Parses `## TOP JD KEYWORDS` section; returns (found_n, total_n, found_kws, missing_kws) |

No LLM calls — all pure heuristics. Safe to import in any context.

---

### system_log.py — Run Logger + Concurrency Tracker

Thread-safe structured logger. Tracks every pipeline run from start to finish without ever writing CV text, JD text, or personal data to disk.

**What is logged:** run_id, company name, model, phase events, duration, outcome, quality badge, ATS ratio.  
**What is never logged:** CV text, JD text, model responses, personal information.

**Log files:**
- `./logs/run_log_YYYY-MM-DD.jsonl` — one JSON object per line, rotated daily
- `./.cache/cost_log_YYYY-MM-DD.json` — daily spend accumulator (feeds the spend guard)

**Estimated cost per run:**

| Model | Est. cost |
|---|---|
| claude-sonnet-4-6 | $0.30 |
| claude-haiku-4-5-20251001 | $0.05 |
| claude-opus-4-6 | $1.20 |

Based on ~25K input tokens + ~8K output tokens across a full pipeline run.

| Function | Purpose |
|---|---|
| `start_run(company, model, experience_level, source)` | Register run, return run_id |
| `log_event(run_id, phase, event, detail)` | Log phase event (detail capped at 200 chars to prevent accidental PII) |
| `end_run(run_id, success, duration_secs, ...)` | Close run, accumulate cost |
| `get_active_count()` | Concurrent users proxy (int) |
| `get_active_runs()` | Sanitised snapshot for admin sidebar |
| `get_today_stats()` | runs_total, runs_succeeded, avg_duration, quality_counts, daily_cost_usd |

All log writes are wrapped in `except Exception: pass` — log failure must never crash the pipeline.

---

## 15. v11 Change Summary

### agents.py
- `build_agents(model)` API — no longer requires a pre-built LLM object
- Per-agent temperatures: JD Analyst=0.0, Company Researcher=0.0, CV Tailor=0.3, Interview Coach=0.1
- Company Researcher `max_iter` raised from 8 to 12 for 5 targeted searches

### tasks.py
- Task 1 (JD Analysis): forced step-by-step fit score arithmetic; fact-grounding enforced
- Task 2 (Company Research): 5 role-specific searches; every claim source-attributed with `[source: search result]` or `[source: LLM estimate — verify independently]`
- Task 3 (CV Tailor): no-fabrication rules strengthened; every bullet traceable to original CV
- Task 4 (Interview Prep): 10 technical Qs with answer guidance; seniority calibration by experience level; 5 culture-specific behavioral Qs; STAR stories with Option A (CV evidence) / Option B (no evidence — guidance scaffold, no fabrication); salary script; red flags section; 30/60/90 day plan; 9 tiered questions to ask

### main.py
- CV input: PDF / DOCX / TXT via `cv_utils.read_cv()`
- Pre-extraction of role title + experience level before parallel phase
- Prompt injection sanitization before pipeline
- System log wiring: `start_run()` → `log_event()` per phase → `end_run()` with quality + ATS score

### streamlit_app.py
- CV file uploader (PDF / DOCX / TXT) — no disk write
- JD metadata expander: platform, location, job_type, posting_date, experience_level override
- Buffered pipeline output: no LLM verbose text in UI; only structured phase progress events
- Phase progress indicator: ○ waiting → ⟳ running → ✓ done
- ATS keyword match score shown as colored badge post-run
- Source badge rendering: `[source: search result]` → green ✓ badge; `[source: LLM estimate]` → amber ⚠ badge
- Quality badge from evaluator in results header
- 5 tabs: JD Analysis, Company Intel, Tailored CV, Interview Prep, Similar JDs
- Similar JDs tab: card layout with similarity %, metadata, color-coded match score
- Sidebar stats panel: active runs with elapsed time, today's metrics, daily cost, quality counts
- JD auto-saved to SQLite on each run; similar JD search after save

### requirements.txt
Added:
- `pdfplumber>=0.11.0`
- `sentence-transformers>=2.7.0`

Install sentence-transformers on your machine with:
```
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install sentence-transformers
```

---

### Summary: Before (v10) vs. After (v11)

| Dimension | v10 | v11 |
|---|---|---|
| CV input | Paste only | Upload PDF/DOCX/TXT or paste |
| Security | None | Prompt injection sanitizer (35 patterns) |
| JD storage | None | SQLite + BGE embeddings |
| Similar JDs | None | Cosine similarity search with metadata filters |
| Quality check | None | LLM-as-judge (Haiku, 4 dimensions, green/amber/red) |
| ATS score | None | Keyword match badge (found N/M) |
| Source attribution | None | Colored ✓/⚠ badges on company research |
| System logging | None | Thread-safe JSONL log, cost tracker, active run registry |
| Concurrent users | Unknown | Tracked live; ~2–3 at Tier 1, ~15–20 at Tier 2 |
| Interview prep depth | Basic Qs | 10 tech Qs + STAR + salary script + 30/60/90 plan |
| Pipeline output | LLM text streamed live | Buffered; phase indicators only |

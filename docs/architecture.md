# Job Intelligence System — Full Architecture & Design Documentation

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Component Breakdown](#3-component-breakdown)
4. [Data Flow & Workflow](#4-data-flow--workflow)
5. [Agent Design](#5-agent-design)
6. [Tool Design](#6-tool-design)
7. [Output Generation Pipeline](#7-output-generation-pipeline)
8. [Design Decisions & Trade-offs](#8-design-decisions--trade-offs)
9. [Technology Comparisons](#9-technology-comparisons)
10. [Known Remaining Weaknesses](#10-known-remaining-weaknesses)
11. [Security & Privacy Analysis](#11-security--privacy-analysis)
12. [Scalability Considerations](#12-scalability-considerations)

---

## 1. Project Overview

**Job Intelligence System** is a multi-agent AI application that transforms a raw job application into a fully prepared interview package. Given a candidate's master CV and a job description, it produces:

| Output | Format | Purpose |
|--------|--------|---------|
| Tailored CV | `.docx` | ATS-optimised, role-specific résumé |
| Intelligence Report | `.pdf` | JD analysis + company research + interview prep |
| Raw Outputs | `.md` | Plain-text reference, copy-pasteable |

The system is powered by **Claude** (Anthropic) via **CrewAI**, with web intelligence gathered using **DuckDuckGo** search and **BeautifulSoup** scraping.

### Core Value Proposition

Traditional job prep is manual, slow, and generic. This system:
- Matches the candidate's exact experience against the JD's ATS keywords
- Gathers live company intelligence (culture, salary, interview rounds)
- Tailors the CV without fabricating any experience
- Generates role-specific technical and behavioural interview questions
- Produces salary negotiation scripts grounded in real market data

---

## 2. System Architecture

### High-Level Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                         USER INPUTS                              │
│  Master CV (txt) │ Job Description (paste) │ Company Name        │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│               PHASE 1 — PARALLEL (ThreadPoolExecutor)            │
│                                                                  │
│  ┌─────────────────────┐    ┌─────────────────────────────────┐  │
│  │  Task 1             │    │  Task 2                         │  │
│  │  JD Analyst         │    │  Company Researcher             │  │
│  │  (no tools)         │    │  (web_search + scrape_page)     │  │
│  │  Crew A             │    │  Crew B                         │  │
│  └──────────┬──────────┘    └──────────────┬──────────────────┘  │
│             └──────────┬───────────────────┘                     │
└──────────────────────────┬───────────────────────────────────────┘
                           │  both outputs available
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│               PHASE 2 — SEQUENTIAL (single Crew)                 │
│                                                                  │
│              ┌──────────────────────────┐                        │
│              │  Task 3                  │                        │
│              │  CV Tailor               │                        │
│              │  context: [Task 1]       │                        │
│              └─────────────┬────────────┘                        │
│                            │                                     │
│                            ▼                                     │
│              ┌──────────────────────────┐                        │
│              │  Task 4                  │                        │
│              │  Interview Coach         │                        │
│              │  context: [1 + 2 + 3]   │                        │
│              └──────────────────────────┘                        │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                    OUTPUT GENERATION LAYER                       │
│                                                                  │
│   tailored_cv text ──► python-docx (pure Python) ──► cv.docx    │
│   all outputs      ──► reportlab               ──► report.pdf   │
│   all outputs      ──► plain write             ──► raw.md       │
└──────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Layer | Technology | Version | Role |
|-------|-----------|---------|------|
| LLM | Claude (Anthropic) | claude-sonnet-4-6 / haiku / opus | Reasoning & generation |
| Agent Framework | CrewAI | 0.80.0 | Multi-agent orchestration |
| LLM Bridge | langchain-anthropic | 0.3.0 | CrewAI → Claude API |
| Web Search | duckduckgo-search | 6.3.7 | Free, keyless search |
| Web Scraping | requests + BeautifulSoup4 | 2.32.3 / 4.12.3 | HTML parsing |
| PDF Generation | reportlab | 4.2.2 | Structured PDF report |
| DOCX Generation | python-docx | 1.1.2 | Formatted Word CV (pure Python) |
| Config | python-dotenv | 1.0.1 | Environment variables |
| Validation | pydantic | 2.10.4 | Type safety (via CrewAI) |
| Runtime | Python 3.11+ | — | Single runtime (no Node.js) |

---

## 3. Component Breakdown

### `main.py` — Entry Point & Orchestrator

Responsibilities:
- Reads user inputs (CV path, JD text, company name, model choice)
- Warns if CV exceeds 1,200 words (token budget risk)
- Builds agents and tasks
- **Runs Tasks 1 and 2 in parallel** via `ThreadPoolExecutor` (Phase 1)
- Runs Tasks 3 and 4 sequentially in a single Crew after Phase 1 completes (Phase 2)
- Delegates to output generators

Output directory uses `<company>_<YYYYMMDD_HHMMSS>` naming — the seconds component prevents collisions even within the same minute.

### `agents.py` — Agent Definitions

Defines four specialised agents. Each agent has:
- **role**: Used by CrewAI as identity in multi-agent communication
- **goal**: What the agent optimises for in every response
- **backstory**: Persona that shapes LLM reasoning style and tone
- **llm**: Shared LLM instance (same model for all agents)
- **tools**: Only agents that need web access get tools
- **allow_delegation=False**: Prevents runaway delegation loops

| Agent | Tools | Delegation |
|-------|-------|-----------|
| JD Analyst | None | Off |
| Company Researcher | web_search, scrape_page | Off |
| CV Tailor | None | Off |
| Interview Coach | web_search | Off |

### `tasks.py` — Task Definitions & Context Graph

Each task embeds the full JD/CV text directly in the description (as f-strings) — this guarantees LLM has primary inputs regardless of context window truncation.

Context graph:
```
Task 1 (JD Analysis)       ──► no dependencies
Task 2 (Company Research)  ──► no dependencies
Task 3 (CV Tailor)         ──► depends on Task 1
Task 4 (Interview Coach)   ──► depends on Task 1 + Task 2 + Task 3
```

Task 4 receives the tailored CV (Task 3) in addition to Tasks 1 and 2, ensuring STAR stories and talking points use the same language and framing as the submitted CV.

### `tools.py` — External Data Tools

Two tools using CrewAI's `@tool` decorator with hardening:

**`web_search(query)`**
- Uses `duckduckgo_search.DDGS` context manager
- Results cached for 30 days keyed by `query + YYYY-MM` (avoids repeated searches for same company)
- Retries up to 3 times with exponential backoff on failure
- Returns explicit `[WARNING: Search returned 0 results...]` on empty results — prevents silent hallucination
- Output format: `Title: ... | URL: ... | Snippet: ...` — structured for LLM consumption

**`scrape_page(url)`**
- **SSRF guard**: validates URL against a 14-domain allowlist before making any HTTP request; unknown/internal URLs are rejected with a clear message
- `requests.get()` with desktop User-Agent header
- BeautifulSoup strips noise tags: script, style, nav, footer, header, aside, form
- Truncated to 6,000 characters (increased from 4,000 to capture more salary/review content)
- Differentiated error messages: timeout vs. HTTP error vs. general failure

Allowlisted domains: `glassdoor.com`, `ambitionbox.com`, `linkedin.com`, `indeed.com`, `naukri.com`, `levels.fyi`, `payscale.com`, `comparably.com`, `teamblind.com`, `reddit.com`, `crunchbase.com`, `techcrunch.com`, `economictimes.indiatimes.com`, `moneycontrol.com`, `yourstory.com`

### `output_generator.py` — Report & CV Generation

**DOCX path**: pure Python via `python-docx`. No Node.js, no subprocess, no temp file.
- Parses CV text line by line: name → contact → section headers → company/role/date lines → bullets → body
- Section headers detected by ALL-CAPS pattern (`r"^[A-Z][A-Z\s/]{2,}$"`) or `## ` prefix
- Navy/blue colour scheme matching the original design
- Horizontal rules inserted via `w:pBdr` XML elements

**PDF path**: pure Python via `reportlab`. `_parse_markdown_to_flowables()` converts agent markdown to flowable objects.
- `_sanitise_for_reportlab()` applied to every line before rendering (headers, bullets, body)
- Sanitisation strips: curly quotes, em/en dashes, `**bold**`/`*italic*` markdown, then escapes `&`, `<`, `>`
- Prevents all previously-seen `xml.parsers.expat.ExpatError` crashes

---

## 4. Data Flow & Workflow

### Step-by-Step Execution Flow

```
[1] STARTUP
    main.py loaded
    .env loaded → ANTHROPIC_API_KEY validated
    User prompted: CV path, JD text, company name, model
    CV word count checked → warn if > 1200 words

[2] AGENT/TASK CONSTRUCTION
    build_llm(model)          → CrewAI LLM wrapper for Claude
    build_agents(llm)         → 4 Agent objects with roles/goals/backstories
    build_tasks(agents, ...)  → 4 Task objects with descriptions and context links

[3] PHASE 1 — PARALLEL (ThreadPoolExecutor, max_workers=2)
    Thread A: Crew([jd_analyst], [task_jd_analysis]).kickoff()
      Input:  JD text + CV text (embedded in description)
      LLM:    Claude generates fit score (rubric-calibrated), keywords, gaps
      Output: ~600-800 token structured markdown → task_jd_analysis.output.raw

    Thread B: Crew([company_researcher], [task_company_research]).kickoff()
      Input:  Company name (in description)
      Tools:  web_search("company glassdoor reviews")  ← cached 30 days
              web_search("company ambitionbox")        ← cached 30 days
              web_search("company news 2024 2025")     ← cached 30 days
              scrape_page(url)  ← allowlist-validated
      LLM:    Claude synthesises web data into structured intel report
      Output: ~600-1000 token report → task_company_research.output.raw

    Both threads complete → results joined → print "✓ JD Analysis / Company Research complete"

[4] PHASE 2 — SEQUENTIAL (single Crew)
    Task 3: CV Tailor
      Input:  Original CV (in description) + Task 1 output (context=[task_jd_analysis])
      LLM:    Claude rewrites CV — no fabrication rules enforced
      Output: Complete tailored CV → task_cv_tailoring.output.raw

    Task 4: Interview Coach
      Input:  CV (in description) + Tasks 1+2+3 outputs (context=[1,2,3])
      Tools:  web_search("company interview experience")
      LLM:    Claude generates questions, STAR stories (aligned with tailored CV),
              salary strategy
      Output: ~700-1000 token interview prep kit → task_interview_prep.output.raw

[5] OUTPUT GENERATION
    generate_cv_docx(tailored_cv, cv_path)
      → python-docx builds Document in memory
      → doc.save(output_path)
      → no temp files, no subprocess

    generate_report_pdf(company, jd, research, prep, pdf_path)
      → _sanitise_for_reportlab() on every line
      → reportlab builds story → PDF with cover, TOC, 3 sections

    writes raw_outputs.md

[6] DONE
    Prints output paths to console
```

### Token Budget Analysis

| Task | Input Tokens (est.) | Output Tokens (est.) |
|------|--------------------|--------------------|
| JD Analysis | ~1,500 (JD + CV) | ~600 |
| Company Research | ~200 (name) + web snippets ~3,000 | ~800 |
| CV Tailor | ~1,500 (CV) + ~600 (Task 1 context) | ~700 |
| Interview Coach | ~1,500 (CV) + ~600 + ~800 + ~700 (contexts) | ~900 |
| **Total** | **~11,600** | **~3,000** |

Typical total: **~14,600 tokens** per run ≈ $0.15–$0.45 depending on model.
Web search results are cached — repeat searches for the same company cost 0 tokens.

---

## 5. Agent Design

### Agent Persona Architecture

CrewAI agents use LLM prompting to simulate specialised expertise. The persona components work as follows:

**Role** → Injected as system identity. Claude reasons from this role when generating outputs. "Senior JD Analyst" vs "HR Generalist" produces measurably different output quality.

**Goal** → The optimisation objective. Drives Claude to produce the specific structured format requested.

**Backstory** → Provides context that activates domain-specific knowledge. "10+ years of AI/ML hiring" + "ATS systems weight heavily" primes Claude to reason about keyword density.

### Why 4 Agents Instead of 1?

A single agent with all four tasks would produce inferior results because:

1. **Context contamination**: A single agent accumulates conversation history, leading to drift and inconsistency between tasks.
2. **Role specialisation**: Each persona primes different reasoning modes. The Company Researcher behaves like an investigator; the CV Tailor behaves like a writer.
3. **Tool scoping**: Only relevant agents get tools, preventing unnecessary tool calls.
4. **Parallelism**: Tasks 1 and 2 are independent and run concurrently in separate Crew instances via `ThreadPoolExecutor`.

### Context Passing Strategy

CrewAI's `context=[task]` parameter appends the referenced task's `.output.raw` to the current task's prompt:

- **Task 3 (CV Tailor)**: receives Task 1 (JD Analysis) → tailors CV to the exact identified keywords and emphasis points
- **Task 4 (Interview Coach)**: receives Tasks 1 + 2 + 3 → generates company-specific questions AND STAR stories that use the same framing as the submitted CV, ensuring interview and CV are consistent

### Fit Score Rubric

The JD Analyst uses an explicit scoring rubric to reduce run-to-run variance:

```
Score = (mandatory requirements met / total mandatory) × 50
      + (preferred requirements met / total preferred) × 30
      + (experience years match: full=20, partial=10, none=0)
      rounded to nearest 5
```

This replaces the previous uncalibrated "0-100 judgment" and produces scores consistent to within ±5 points across runs.

---

## 6. Tool Design

### DuckDuckGo Search (`web_search`)

**Why DuckDuckGo?**
- Zero cost, no API key required
- Sufficient result quality for company research
- Snippets from Glassdoor/AmbitionBox often appear in search results even when full pages are JS-rendered

**Hardening applied:**
- 30-day result cache (JSON files in `.cache/company_research/`, keyed by query + month)
- 3-retry with exponential backoff: 2s → 4s → 8s
- Empty results return an explicit warning string instead of the silent `"No results found."` that previously caused hallucination

**Limitations:**
- Can be rate-limited under heavy use
- No date filtering
- Result quality lower than paid APIs (Tavily, SerpAPI)

### Web Scraper (`scrape_page`)

**SSRF guard**: Every URL is validated against a hardcoded allowlist of 14 trusted domains before any HTTP request is made. URLs that fail validation return a descriptive rejection message to the agent, which then falls back to web_search snippets.

**Noise reduction**: removes `<script>`, `<style>`, `<nav>`, `<footer>`, `<header>`, `<aside>`, `<form>` — ~60% of modern HTML but zero useful content.

**6,000-character limit** (increased from 4,000): captures more salary and review data that appears deeper in pages.

**Differentiated failures**: timeout, HTTP error (with status code), and general exception are reported distinctly so the agent can choose the appropriate fallback strategy.

**Limitation**: Sites like Glassdoor and LinkedIn render reviews client-side via JavaScript. `requests.get()` only fetches server-rendered HTML. In practice, review snippets often appear in meta tags and JSON-LD in the initial HTML, so partial data is still retrievable.

---

## 7. Output Generation Pipeline

### Pure-Python Architecture

All output generation is now in a single Python runtime:

```
Python (python-docx)  →  DOCX  (formatted CV)
Python (reportlab)    →  PDF   (3-section intelligence report)
Python (write)        →  MD    (plain-text backup)
```

The original Node.js subprocess approach (`generate_cv.js` via `subprocess.run(["node", ...])`) has been replaced entirely. This eliminates:
- Node.js as a runtime dependency
- `cv_temp.txt` hardcoded temp file (race condition + PII-on-disk risk)
- Subprocess bridge failure modes (Node.js not on PATH, npm not initialised, stderr swallowed)

### DOCX Generation with python-docx

`generate_cv_docx()` parses CV text line by line through a priority cascade:

```
1. First non-blank line          → candidate name  (20pt navy bold, centred)
2. Line with @/|/phone pattern   → contact line    (9pt grey, centred, blue HR)
3. ALL-CAPS or ## prefix         → section header  (11pt navy bold + navy HR)
4. Contains | (no leading -)     → company/role    (10pt navy bold + grey date)
5. Starts with - or •            → bullet point    (9.5pt, List Bullet style)
6. Starts with digit.            → numbered item   (9.5pt, List Number style)
7. Anything else                 → body paragraph  (9.5pt)
```

Horizontal rules are injected via `w:pBdr` XML elements (python-docx's native HR support).

### PDF Generation with ReportLab

`_parse_markdown_to_flowables()` converts agent markdown to ReportLab flowable objects.

`_sanitise_for_reportlab()` is applied to **every line** before it becomes a `Paragraph`:

| Transformation | Reason |
|---------------|--------|
| Curly quotes → straight | ReportLab XML parser chokes on `"` `"` `'` `'` |
| Em/en dashes → `--` / `-` | Avoids encoding issues in Helvetica font |
| `**text**` → `text` | Strip markdown bold (not valid XML) |
| `*text*` → `text` | Strip markdown italic |
| `&` → `&amp;` | XML escape |
| `<` → `&lt;` | XML escape |
| `>` → `&gt;` | XML escape |

Previously, escaping was only applied to body paragraphs. Headers and bullets that contained `&` or `<` (e.g., salary ranges like `>₹30 LPA`, company names like `Infosys & BPO`) crashed the renderer.

---

## 8. Design Decisions & Trade-offs

### Decision 1: Two-Phase Parallel + Sequential Execution

**Chosen**: Phase 1 runs Tasks 1 and 2 in parallel threads; Phase 2 runs Tasks 3 and 4 sequentially in a single Crew.

**Why not single sequential Crew?**
Tasks 1 and 2 have zero data dependencies — running them sequentially wastes 60–120 seconds of wall-clock time.

**Why not `Process.hierarchical`?**
Hierarchical adds a manager LLM that routes tasks dynamically. For a predefined pipeline, this adds latency and non-determinism with no benefit.

**Why `ThreadPoolExecutor` instead of `asyncio`?**
CrewAI 0.80.0's `kickoff()` is synchronous. `asyncio.to_thread()` or `ThreadPoolExecutor` are equivalent here; `ThreadPoolExecutor` is more explicit about the two-thread model.

**Trade-off**: Two separate Crew instances mean Phase 1 tasks don't share a conversation context with each other. This is intentional — they have no shared context to benefit from.

### Decision 2: Full JD/CV Embedded in Task Description

**Chosen**: Embed the full JD and CV text directly in each task's `description` f-string.

**Why?**
- Guarantees LLM always has the primary inputs regardless of context window truncation
- CrewAI's context passing can be lossy or truncated for large inputs
- More explicit, more debuggable

**Trade-off**: Same CV text sent in Tasks 1, 3, and 4, increasing total token count by ~3,000 tokens. Prompt caching (setting `cache_control` on the CV content block) would recover most of this cost.

### Decision 3: Claude Exclusively

**Chosen**: Claude (Anthropic) exclusively.

**Why Claude?**
- Superior instruction following for structured markdown output
- Excellent at role-playing personas (critical for agent backstories)
- Strong at long-context document understanding (CV + JD analysis)
- claude-sonnet offers better cost/quality ratio than GPT-4o for this use case

**Trade-off**: Single-provider lock-in. If Anthropic API is down, the system is fully unavailable.

### Decision 4: `allow_delegation=False` on All Agents

**Chosen**: Delegation disabled for all four agents.

**Why?**
- Prevents runaway multi-agent loops (previously observed consuming 15–20 minutes and ~10,000 tokens on nothing)
- The task structure handles inter-agent communication via explicit context passing

### Decision 5: DuckDuckGo for Web Search

**Chosen**: `duckduckgo_search` (free, no API key).

**Trade-off**: Less reliable than paid APIs, can be rate-limited. Acceptable for a single-user personal tool. For production, Tavily or SerpAPI is recommended.

### Decision 6: CV "No Fabrication" Rule

**Chosen**: Explicit RULES section in CV Tailor task description prohibiting invented content, with the original CV embedded as the reference ground truth.

**Why?** Ethical and legal risk — LLMs will optimise toward "maximise relevance" by inventing experience if not explicitly prohibited.

**Limitation**: Reduces but does not eliminate hallucination. Human review of the tailored CV is still recommended.

---

## 9. Technology Comparisons

### CrewAI vs. LangGraph vs. AutoGen vs. Raw Prompting

| Feature | CrewAI | LangGraph | AutoGen | Raw Prompting |
|---------|--------|-----------|---------|--------------|
| Multi-agent | ✅ Native | ✅ Via nodes | ✅ Native | ❌ Manual |
| Context passing | ✅ Automatic | ✅ State graph | ✅ GroupChat | ❌ Manual |
| Sequential flow | ✅ Built-in | ✅ Edges | ⚠️ Complex | ✅ Simple |
| Parallel tasks | ⚠️ Via threads | ✅ Full control | ✅ Supported | ❌ Manual |
| Agent personas | ✅ Role/Goal/Backstory | ❌ Not native | ✅ System prompts | ✅ System prompts |
| Tool integration | ✅ @tool decorator | ✅ Tool nodes | ✅ FunctionCall | ✅ Manual |
| Learning curve | Low | Medium | Medium | Low |
| Determinism | Medium | High | Low | High |
| Best for | Defined pipelines | Complex state machines | Open-ended collaboration | Simple single-agent |

**Verdict for this project**: CrewAI is the right choice. The pipeline is well-defined and benefits from role specialisation — exactly what CrewAI is optimised for.

### reportlab vs. WeasyPrint vs. fpdf2 vs. pdfkit

| Library | API Style | HTML Support | Python Native | Complexity |
|---------|-----------|-------------|--------------|-----------|
| reportlab | Programmatic flowables | ❌ | ✅ | High |
| WeasyPrint | HTML/CSS → PDF | ✅ | ✅ | Low |
| fpdf2 | Coordinate-based | ❌ | ✅ | Medium |
| pdfkit | wkhtmltopdf wrapper | ✅ | ❌ (binary dep) | Low |

**Why reportlab?** Full control over layout, styles, and colour without an HTML/CSS translation layer. Agent output is markdown, not HTML — building flowables directly is more reliable.

### python-docx vs. docx (npm)

| Feature | python-docx (current) | docx (npm) (removed) |
|---------|----------------------|---------------------|
| Language | Python | JavaScript |
| Subprocess needed | ❌ | ✅ |
| Temp file (PII risk) | ❌ | ✅ (`cv_temp.txt`) |
| Race condition risk | ❌ | ✅ (hardcoded path) |
| Node.js dependency | ❌ | ✅ |
| Deployment complexity | Low | High |

Switched to python-docx — eliminates the subprocess, the temp file, and the Node.js runtime requirement entirely.

---

## 10. Known Remaining Weaknesses

The following issues were identified and have **not yet been fixed**. The resolved issues from the original list are documented in [struggles.md](struggles.md).

### Open Issue 1: No Retry for LLM API Calls

Web search retries on network failure, but if the Anthropic API returns a 429 (rate limit) or 5xx mid-run, the Crew raises an unhandled exception and all progress is lost. There is no checkpointing.

**Impact**: A 5–10 minute run can fail with no partial output saved.

**Recommended fix**: Wrap `crew.kickoff()` in a retry loop with exponential backoff. Save `task.output.raw` to a checkpoint file after each task completes so a retry can resume from the last checkpoint.

### Open Issue 2: No Structured Output Validation

Agents are instructed to return specific markdown formats but there is no schema enforcement. LLMs deviate ~10–15% of the time — adding preamble text, changing heading levels, or skipping sections.

**Impact**: PDF section headings may render as body text; fit score may appear in an unparsed format.

**Recommended fix**: Use CrewAI's `output_pydantic` parameter with Pydantic models for each task's expected schema. CrewAI will retry if the output fails validation.

### Open Issue 3: No Progress Feedback During Long Runs

The user sees raw CrewAI verbose logs for 5–10 minutes. The parallel phase does print `"✓ JD Analysis complete"` / `"✓ Company Research complete"` but there is no structured progress bar.

**Recommended fix**: Add `rich` library with `Progress` + `SpinnerColumn` bars, and hook into CrewAI's `step_callback` for per-tool-call granularity.

### Open Issue 4: JavaScript-Rendered Sites Not Scraped

Glassdoor, LinkedIn, and Naukri render reviews client-side. `requests.get()` retrieves only the initial HTML shell. The Company Researcher compensates by relying on DuckDuckGo snippet aggregation across multiple queries.

**Recommended fix**: Integrate Firecrawl or Jina AI Reader for JS-rendered content extraction. Alternatively, partner with data providers for structured review data.

### Open Issue 5: CV PII in Anthropic API Logs

The full CV (name, phone, email, employment history) is sent to Anthropic's API in every task prompt. Anthropic's data processing policy covers this for API usage, but it is worth noting for users with sensitive employment history.

**For production**: Add a privacy notice in the CLI banner. Consider PII scrubbing before sending (replace real email/phone with placeholders for Tasks 1 and 4 where those fields don't affect output quality).

---

## 11. Security & Privacy Analysis

### Threat Model (Current State)

| Threat | Status | Mitigation |
|--------|--------|-----------|
| API key leak via git | **Mitigated** | `.gitignore` now excludes `.env` |
| SSRF via scrape_page | **Mitigated** | URL allowlist in `tools.py:50` — 14 trusted domains only |
| PII in temp file | **Mitigated** | No temp file — python-docx generates DOCX in memory |
| Race condition (cv_temp.txt) | **Mitigated** | Temp file eliminated by python-docx migration |
| Empty search → hallucination | **Mitigated** | Explicit `[WARNING]` string returned, not silent empty |
| Prompt injection via JD | **Open** | Input length check on CV only; JD not validated |
| Stale company data | **Partially mitigated** | 30-day cache with month key — data is at most ~60 days old |
| CV PII sent to Anthropic API | **Open** | Inherent to the system; covered by Anthropic's privacy policy |

### Privacy Considerations

**Data handled:**
- Full CV text (name, email, phone, employment history, education) — highly sensitive PII
- Job description — typically public
- Company research data — public information

**Data flow:**
1. CV sent to Anthropic API (processed per [Anthropic's privacy policy](https://www.anthropic.com/privacy))
2. CV embedded in LLM prompts — appears in CrewAI `verbose=True` console output
3. All outputs saved to `outputs/` directory in plaintext — included in `.gitignore`
4. Search results cached in `.cache/` — no PII, public data only

**For multi-user deployment:**
- Encrypt outputs at rest (AES-256 or OS-level disk encryption)
- Scope `outputs/` and `.cache/` to per-user directories
- Do not log raw CV text in production (disable `verbose=True`)
- Add a privacy notice before first run

---

## 12. Scalability Considerations

### Current Architecture: Single User, Local CLI

The current design is deliberately simple — a single-user CLI tool. It is **not** designed for concurrent use.

### Scaling to a Web Application

If converting to a FastAPI web service:

1. **Async endpoints**: `await crew.kickoff_async()` — CrewAI supports async execution; Phase 1 parallelism can be replaced with `asyncio.gather()`
2. **Job queues**: Celery + Redis to queue crew runs — one LLM call per task means queuing is essential under load
3. **Per-user isolation**: use `uuid.uuid4()` as a run ID; scope output dirs and cache dirs per user/run
4. **Database for results**: replace `outputs/` directory with PostgreSQL + S3/GCS for file storage
5. **Rate limiting**: Anthropic API has per-minute token limits — add a token budget manager per tenant
6. **Remove `verbose=True`**: replace with structured logging (structlog or loguru)

### Estimated Concurrent Capacity (Anthropic Claude API)

- claude-sonnet rate limit: ~100k tokens/minute
- Each run: ~14,600 tokens
- Theoretical max concurrent runs: ~6–7 (without queueing)
- With Anthropic Batch API: 50%+ cost reduction for non-real-time use cases

---

*Document last updated: April 2026*
*Reflects: Job Intelligence v1.1 — parallel execution, python-docx, SSRF fix, search cache, calibrated fit score, full context chain*

# Job Intelligence System — Struggles, Failures & Lessons Learned

> A brutally honest account of what went wrong, why it went wrong, and what was learned while building this system. This document is written for posterity and for anyone who wants to understand the real cost of building LLM-powered multi-agent applications.

---

## Table of Contents

1. [Getting CrewAI to Actually Follow Format Instructions](#1-getting-crewai-to-actually-follow-format-instructions)
2. [LLM Hallucination in the CV Tailor](#2-llm-hallucination-in-the-cv-tailor)
3. [Context Window Overflow](#3-context-window-overflow)
4. [Web Scraping Completely Failing on Major Sites](#4-web-scraping-completely-failing-on-major-sites)
5. [PDF Generation Breaking on LLM Output](#5-pdf-generation-breaking-on-llm-output)
6. [The Node.js Subprocess Nightmare](#6-the-nodejs-subprocess-nightmare)
7. [DuckDuckGo Rate Limiting Mid-Run](#7-duckduckgo-rate-limiting-mid-run)
8. [Agent Delegation Loops](#8-agent-delegation-loops)
9. [CrewAI Version Hell](#9-crewai-version-hell)
10. [Task Context Not Passing Correctly](#10-task-context-not-passing-correctly)
11. [The Fit Score Inconsistency Problem](#11-the-fit-score-inconsistency-problem)
12. [Output Directory Naming Collisions](#12-output-directory-naming-collisions)
13. [The "DONE" Input Termination Hack](#13-the-done-input-termination-hack)
14. [Interview Coach Giving Generic Answers](#14-interview-coach-giving-generic-answers)
15. [General Lessons About Building Agentic AI](#15-general-lessons-about-building-agentic-ai)

---

## 1. Getting CrewAI to Actually Follow Format Instructions

### The Problem

The first working prototype had agents that returned beautifully written prose — but in completely different formats on every run. The JD Analyst would sometimes return a numbered list, sometimes bullet points, sometimes headers, sometimes just paragraphs. The PDF generator would then produce broken or ugly output because it expected `## FIT SCORE` and got `**FIT SCORE:**` instead.

**Example of what went wrong:**

Run 1 output:
```
The fit score for this candidate is approximately 72 out of 100. The candidate demonstrates strong Python skills which aligns with the job requirements...
```

Run 2 output:
```
## FIT SCORE
72/100 — Strong Python background, weak on LangChain

## TOP JD KEYWORDS
- Python
- LangChain
...
```

### The Struggle

Spent several hours tweaking system prompts. The first instinct was to use "softer" instructions like "please structure your output as follows". This didn't work. Moved to "Your output MUST be in this exact format" with the literal template pasted. This was better but still inconsistent.

The real fix came from being extremely literal — pasting the exact output template WITH example content into the task description, not just the structure. The LLM treats filled-in examples as far stronger guidance than structural descriptions.

### What Was Learned

- **LLMs follow examples far better than instructions.** Always include a filled-in template, not just field names.
- The word "MUST" in all-caps genuinely improves compliance — LLMs are trained on human text where caps convey emphasis.
- Even with perfect prompting, ~10-15% of runs will produce minor format deviations. Build a tolerant parser, not a strict one.
- CrewAI's `output_pydantic` parameter would have been the correct engineering solution, but was discovered late in development.

---

## 2. LLM Hallucination in the CV Tailor

### The Problem

Early versions of the CV Tailor agent invented experience. The most alarming example: the candidate's CV mentioned building a chatbot, and the CV Tailor rewrote it to say "Led a team of 5 engineers to build a production-grade conversational AI system handling 50,000 daily users." None of that was in the original CV.

This was a critical failure. A candidate submitting a CV with fabricated metrics could face serious professional consequences.

### The Struggle

The initial agent backstory said: "You are an expert CV writer who enhances candidates' experience to maximise their chances." The word **"enhances"** was interpreted as license to invent.

First fix: changed "enhances" to "rewrites while preserving factual accuracy." Still hallucinated.

Second fix: Added a RULES section to the task description:
```
RULES — YOU MUST FOLLOW THESE:
1. Do NOT invent any experience, project, or skill not in the original CV.
2. Every bullet point must be directly traceable to the original CV.
```

Still got occasional inflation (adding realistic-sounding numbers to existing bullets).

Final fix: Added the original CV text into the same task and told the LLM to "treat the original CV as sacred — every fact, number, and date must appear in the original or not appear at all."

### What Was Learned

- **LLMs will optimise toward your stated goal even if that means fabricating.** "Maximise relevance" gets interpreted as "add whatever makes it most relevant."
- Negative constraints ("do NOT") work better than positive constraints ("only use facts from") — both together work best.
- Including the reference document (original CV) in the same context as the rewriting task creates an implicit ground-truth check.
- There is no fully reliable way to prevent hallucination — human review of the tailored CV is still essential.

---

## 3. Context Window Overflow

### The Problem

For candidates with very long CVs (3–4 pages of dense text), the system would fail partway through with a context window error, or produce truncated/degraded output in later tasks.

The issue: Task 3 (CV Tailor) receives the original CV text (~2,000 tokens) PLUS Task 1's JD Analysis output (~600 tokens) as context. For long CVs, this pushed the prompt toward Claude's output limits, leaving little room for the tailored CV output.

### The Struggle

First observed when a candidate with 8 years of experience and 4 pages of CV submitted their document. Task 3 produced a CV that silently dropped the last 2 jobs from the experience section.

The root cause: f-string embedding of the CV text means the full CV is sent on EVERY task (Tasks 1, 3, and 4). For a 2,000-token CV, that's 6,000 tokens of repeated content across the pipeline.

Mitigation attempted:
- Added a note in the README to keep CVs under 1,500 words
- Added input length validation in `read_cv()` (never actually implemented — still a flaw)
- Switched from Haiku to Sonnet as the default model (larger context, better compression)

### What Was Learned

- **Token budgeting is a first-class engineering concern for LLM pipelines.** Calculate worst-case token counts for all inputs before designing prompts.
- Embedding the full CV 3 times (in Tasks 1, 3, 4) was the wrong design — should pass once and reference via context.
- Claude Sonnet handles long-context tasks significantly better than Haiku for this use case.
- Always test with maximum-length inputs (the longest CV a user might realistically submit) as a stress test.

---

## 4. Web Scraping Completely Failing on Major Sites

### The Problem

Glassdoor, LinkedIn, and Naukri all return JavaScript-rendered content. `requests.get()` fetches the server-side HTML, which for these sites is mostly empty placeholder divs and a `<script>` tag that loads React.

The Company Researcher agent would call `scrape_page("https://glassdoor.com/...")` and get back:

```html
<div id="app"></div>
<script src="/static/bundle.js"></script>
```

After BeautifulSoup extraction:
```
(empty or just navigation text)
```

### The Struggle

First approach: try Playwright or Selenium for JS rendering. This worked but added a massive dependency (Chromium browser install) and made the tool 30× slower (3–4 seconds per page vs 100ms).

Second approach: look for API endpoints. Glassdoor has no public API. AmbitionBox has no official API. LinkedIn has a heavily restricted API.

Final approach: rely on DuckDuckGo **snippets** instead of full scraping. The search snippet often contains the most critical sentence from the review (rating, headline, one review sentence). Then use scraping only for sites that render server-side (company career pages, news articles).

This worked well enough: the combination of multiple short snippets across 5-6 search queries gave the agent enough data to synthesise a useful company report.

### What Was Learned

- **Most valuable data sites (Glassdoor, LinkedIn) are JavaScript-rendered and cannot be scraped with `requests`.** This is a fundamental constraint.
- Search snippet aggregation (5 snippets × 5 queries = 25 snippets) is often as informative as full-page scraping for summarisation tasks.
- For production systems, use dedicated data providers: Glassdoor's data can be accessed via partnership, or through services like Bright Data.
- `scrape_page` is most useful for career pages, press releases, and news articles — not review platforms.

---

## 5. PDF Generation Breaking on LLM Output

### The Problem

The `_parse_markdown_to_flowables()` function crashed regularly when agent output contained XML special characters that ReportLab couldn't parse. The LLM output often included:

- `&` in company names (e.g., "Infosys & BPO")
- `<` and `>` in salary ranges (e.g., ">₹30 LPA")
- Unicode characters (₹, em-dashes, curly quotes)
- Markdown bold `**text**` that ReportLab tried to parse as XML tags

Error seen:
```
xml.parsers.expat.ExpatError: not well-formed (invalid token)
```

### The Struggle

First fix: added `&amp;`, `&lt;`, `&gt;` escaping for body paragraphs only. Missed the case where headers also contained these characters.

Second fix: extended escaping to all paragraph types. New problem: headers that used HTML-like formatting (bold via `**`) still broke.

Third fix: added a `clean_line()` function that stripped markdown bold/italic markers before passing to ReportLab. The `**text**` and `*text*` patterns were stripped with regex.

Final remaining issue: ReportLab's `Paragraph` renderer still occasionally chokes on curly quotes (`"` and `"`) that Claude uses naturally. Workaround: replace with straight quotes before rendering.

Total debugging time for PDF rendering: ~4–5 hours across multiple sessions.

### What Was Learned

- **LLM output is structurally unpredictable even with strict formatting prompts.** Never pass raw LLM output to a strict parser (XML, JSON, HTML) without sanitisation.
- ReportLab's `Paragraph` uses a mini-XML renderer — treat it like you're building an XML document, with all the escaping that implies.
- Unicode from LLMs (₹, em-dash, curly quotes) requires explicit handling. Always test with actual LLM output, not handwritten test fixtures.
- Build a "nuclear option" fallback: if `_parse_markdown_to_flowables()` raises, fall back to writing the raw text as a single Paragraph.

---

## 6. The Node.js Subprocess Nightmare

### The Problem

The original design called for `python-docx` for DOCX generation. After spending a day implementing it, the output was functional but visually poor — inconsistent font sizes, wrong indentation for nested bullets, and the company name wasn't rendering at the right size.

Switched to the `docx` npm package which has a more expressive API for complex layout. This worked well — but introduced the subprocess bridge.

The subprocess had several failure modes:

1. **Node.js not on PATH**: On first test on a different machine, `node generate_cv.js` failed because the PATH didn't include the Node.js binary directory.
2. **npm not initialised**: Running the script before `npm install` gave a confusing `Cannot find module 'docx'` error.
3. **Windows path with spaces**: `subprocess.run(["node", js_script, txt_path, output_path])` failed when `output_path` contained spaces in the folder name (e.g., "Persistent Systems GenAI").
4. **Crash with no error message**: When `generate_cv.js` crashed, `result.returncode != 0` caught it but `result.stderr` was empty — the actual error was in `result.stdout`.

### The Struggle

Fixing path-with-spaces: wrapped `txt_path` and `output_path` in the subprocess call with explicit string arguments (they're already separate list items, which handles spaces correctly). But the Windows CMD invocation had different behaviour than bash.

Added explicit stderr + stdout capture:
```python
result = subprocess.run(
    ["node", js_script, txt_path, output_path],
    capture_output=True,
    text=True,
)
if result.returncode != 0:
    raise RuntimeError(f"generate_cv.js failed:\n{result.stderr}\n{result.stdout}")
```

The combined stderr + stdout in the error message finally made debugging tractable.

### What Was Learned

- **Mixed-language pipelines multiply failure points.** Every language boundary is a place where errors can occur, be swallowed, or be mis-reported.
- Subprocess calls must capture BOTH stdout and stderr; Node.js errors often appear in stdout, not stderr.
- Should have stayed with `python-docx` and invested the time to fix the formatting issues there instead.
- Always document runtime dependencies prominently in the README and provide a `check_deps()` function that validates all runtimes before starting the main flow.

**Status: RESOLVED** — DOCX generation was migrated to `python-docx`. The Node.js subprocess, `generate_cv.js`, `cv_temp.txt`, and `node_modules/` are no longer required. The `cv_temp.txt` race condition and PII-on-disk risk are eliminated as a consequence.

---

## 7. DuckDuckGo Rate Limiting Mid-Run

### The Problem

During testing with multiple consecutive runs (5+ in an hour), DuckDuckGo would start returning empty results or throwing connection errors. The `web_search` tool would return `"No results found."` — and the Company Researcher would then synthesise a report based on nothing.

The worst case: the agent would confidently generate fake salary data and interview processes with no grounding in real data. The output looked plausible but was entirely hallucinated.

### The Struggle

No retry logic existed. Added:
```python
try:
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=5):
            results.append(...)
    return "\n---\n".join(results) if results else "No results found."
except Exception as e:
    return f"Search failed: {str(e)}"
```

But the `except` block meant the agent received a failure message and continued anyway — generating hallucinated content without flagging it as uncertain.

Better approach would be: raise the exception, let CrewAI handle retry, or add a minimum-results check: if fewer than 2 results returned, add a clear `[WARNING: Search returned limited results — data below may be incomplete]` prefix.

### What Was Learned

- **"No results found" is just as dangerous as an error in an agentic system.** The agent will fill the void with hallucination.
- Tools should differentiate between "empty results" (soft failure) and "error" (hard failure) and communicate uncertainty to the LLM.
- Rate limiting of free APIs during development should be expected — build retry logic from day one.
- For production, paid search APIs (Tavily, SerpAPI) with proper rate limit handling are essential.

**Status: RESOLVED** — `web_search` now has 3-retry with exponential backoff, a 30-day result cache (eliminates repeated searches entirely), and returns an explicit `[WARNING: Search returned 0 results...]` string instead of the silent empty that caused hallucination.

---

## 8. Agent Delegation Loops

### The Problem

In an early version where `allow_delegation=True`, the Company Researcher agent decided to delegate the "find salary data" subtask to the JD Analyst agent (which had no tools). The JD Analyst, unable to complete the task, delegated back. This loop ran until CrewAI hit its maximum iteration limit.

Result: 15–20 minutes of wasted LLM calls, ~10,000 tokens consumed doing nothing, and no useful output.

### The Struggle

CrewAI's delegation system is based on agents deciding when to ask for help. With a system prompt that says "you are specialised in X," the agents generally stay in lane — but edge cases trigger delegation.

The fix was simple: `allow_delegation=False` on all agents. Given that the task structure already handles context passing explicitly, there's no benefit from allowing delegation.

### What Was Learned

- **For pipelines with well-defined task structures, disable delegation entirely.** Delegation is useful when the task is open-ended; it's harmful when the pipeline is pre-defined.
- Always set a maximum iteration count in CrewAI (`max_iter` parameter) as a circuit breaker.
- Monitor token consumption early in development — unexpected spikes indicate runaway loops.

---

## 9. CrewAI Version Hell

### The Problem

CrewAI's API changed significantly between versions 0.60.x, 0.70.x, and 0.80.x. The initial implementation used 0.68.0 syntax. When upgrading to 0.80.0 (which was required for a bug fix), the following broke:

- `Task.output` was renamed/restructured — `task.output.raw` vs `task.output`
- `LLM` constructor parameter `model` changed to require the `anthropic/` prefix
- `Process.sequential` context-passing behaviour changed (Tasks no longer automatically received previous task output unless `context=[]` was set)
- `crewai.tools.tool` decorator moved from `crewai` to `crewai.tools`

### The Struggle

The upgrade broke the system silently in the worst possible way: the crew ran successfully but context wasn't being passed, so Tasks 3 and 4 produced generic output ignoring the JD analysis and company research.

This took hours to diagnose because the output looked plausible — it was only when comparing outputs side by side that the lack of company-specific content became obvious.

### What Was Learned

- **Pin dependency versions immediately and test upgrades explicitly.** The current `requirements.txt` pins to `crewai==0.80.0` — this should have been done from day one.
- Add an integration test that checks for company-specific content in Task 4 output (e.g., assert company name appears in the interview questions).
- Read the CrewAI changelog before upgrading.
- The `crewai` library is actively developed and breaking changes are common — treat it like a beta dependency.

---

## 10. Task Context Not Passing Correctly

### The Problem

Related to the version issue above but distinct: even with correct `context=[task_jd_analysis]` on the CV Tailor task, the task description (which included the raw JD/CV text via f-string) was sometimes longer than the context window allowed, causing the CrewAI context-passing mechanism to silently truncate or drop the Task 1 output.

The symptom: the CV Tailor produced a CV that matched the JD keywords in the task description but missed the nuanced "Points to Emphasise" from Task 1.

### The Struggle

CrewAI's context passing prepends the referenced task's output to the current task's prompt. But if the task description itself is already 2,000+ tokens, there may not be room for both the description AND the context.

Debugging this required reading CrewAI's internal prompt construction (using `verbose=True` and inspecting the full prompt sent to the LLM).

The fix: reduce the task description token count by removing example outputs from the format specification, and trust that the f-string-embedded CV text was sufficient.

### What Was Learned

- **CrewAI's context passing is additive — it increases prompt length.** Budget for context output tokens when designing task descriptions.
- `verbose=True` is essential for debugging — it reveals the actual prompts sent to the LLM.
- For debugging context passing specifically, temporarily log `task.output.raw` after each task completes and verify it contains what you expect before the next task runs.

---

## 11. The Fit Score Inconsistency Problem

### The Problem

The JD Analyst was asked to produce a "0-100 fit score." Testing revealed the same CV + JD pair produced scores ranging from 62 to 81 across 10 runs. Temperature in LLMs introduces randomness — the fit score was meaningless.

Worse: the score influenced how confidently candidates interpreted their chances. A score of 62 vs 81 for the same profile against the same role is misleading.

### The Struggle

Multiple attempts to stabilise the score:
1. Set LLM temperature to 0 via CrewAI LLM parameters — CrewAI's anthropic integration didn't cleanly support this without digging into langchain-anthropic internals.
2. Added a detailed rubric (50 points for hard requirements, 30 for preferred, 20 for experience) — reduced variance to ±5 points but not eliminated.
3. Added "explain your scoring to the nearest 5 points" — helped force the LLM to think in increments, reducing wild swings.

The fit score is still not fully reliable. The right fix (structured output with explicit scoring rubric in Pydantic model) was never implemented.

### What Was Learned

- **Asking an LLM for a precise number is asking for false precision.** A score of 72/100 implies accuracy that doesn't exist.
- For ranking/scoring tasks, use categorical labels (Strong Fit / Moderate Fit / Weak Fit) rather than numbers — they're more honest about the inherent uncertainty.
- Temperature control is important for reproducibility; ensure LLM construction properly threads temperature through to the API call.

---

## 12. Output Directory Naming Collisions

### The Problem

In early versions, the output directory was just `outputs/<company_name>/`. Running the system twice for the same company would overwrite the previous outputs silently.

This was discovered the hard way when comparing two different JDs for the same company — the second run overwrote the first without warning.

### The Struggle

Added timestamp to directory name: `outputs/<company_name>_<YYYYMMDD_HHMM>/`. This prevents collisions between runs within the same minute.

But: what about running twice within the same minute? Unlikely but possible. The proper fix is to check for directory existence and increment a counter, or use a UUID suffix.

### What Was Learned

- **File system output directories need collision-resistance.** Timestamps work for human use; UUIDs work for programmatic use.
- Always create output directories with `exist_ok=False` if you want to fail fast rather than silently overwrite.
- Establish output directory conventions early — changing them later breaks existing users' directory structures.

---

## 13. The "DONE" Input Termination Hack

### The Problem

Reading multi-line job descriptions from stdin is awkward. The initial implementation used Ctrl+D (EOF) to end input, which:
- Doesn't work on Windows PowerShell (Ctrl+Z is Windows EOF, but behaves inconsistently)
- Confused users who aren't familiar with terminal input conventions
- On some terminals, crashed the Python process instead of gracefully ending input

### The Struggle

Tried several approaches:
1. Ctrl+D / Ctrl+Z — platform-dependent, confusing
2. Empty line as terminator — broken because users paste JDs with blank lines between sections
3. Double newline — same problem
4. File path input instead of paste — better UX but adds friction for the common case
5. "DONE" sentinel on its own line — chosen as the most explicit, platform-independent solution

The "DONE" sentinel is inelegant but reliable across Windows, Mac, and Linux.

### What Was Learned

- **CLI input UX matters.** A 5-minute analysis tool that confuses users at the input step loses its value immediately.
- Multi-line input is genuinely hard to do well in a terminal. For a web UI, a textarea input would eliminate this problem entirely.
- Explicit sentinels ("DONE") are better than control characters for user-facing tools.

---

## 14. Interview Coach Giving Generic Answers

### The Problem

The Interview Coach's technical questions were often too generic:
- "Explain the difference between supervised and unsupervised learning"
- "What is a neural network?"

These are reasonable GenAI questions but they don't demonstrate knowledge of the specific company or role. They could have been generated for any ML role at any company.

### The Struggle

Root cause: the Interview Coach only received JD Analysis (Task 1) and Company Research (Task 2) context, but the web search results for interview experiences were often empty or generic ("Company X has 2-3 technical rounds").

Two improvements made a meaningful difference:
1. Requiring the company name in the tool query: `web_search(f"{company_name} interview experience AI engineer")`
2. Adding explicit instruction: "Generate questions based on the SPECIFIC tech stack mentioned in the JD, not general AI knowledge"

Still not perfect. The biggest remaining gap: the Interview Coach doesn't know which specific projects the candidate will be asked about, because it doesn't see the tailored CV (Flaw #10 in the architecture doc).

### What Was Learned

- **Context specificity drives output specificity.** Generic inputs produce generic outputs — this is the core challenge of personalisation in LLM systems.
- Tool queries must include company/role context; vague queries like `"interview questions for AI engineer"` return generic content.
- The agent design has a logical gap: the person coaching for an interview should know what's on the CV being presented — connecting Task 3 output to Task 4 context is the missing link.

**Status: RESOLVED** — Task 4's context now includes Task 3: `context=[task_jd_analysis, task_company_research, task_cv_tailoring]`. The Interview Coach sees the tailored CV and generates STAR stories and talking points that align with its language and framing.

---

## 15. General Lessons About Building Agentic AI

### On LLM Reliability

- **LLMs are probabilistic, not deterministic.** The same prompt produces different outputs. Build systems that tolerate output variation.
- **Format compliance degrades with output length.** The longer the expected output, the more likely an LLM is to deviate from the format mid-way.
- **Hallucination is not an edge case.** It is the default. Every piece of LLM output that touches real-world facts must be treated as potentially wrong until verified.

### On Multi-Agent Systems

- **More agents ≠ better output.** Each additional agent adds latency, cost, and a new failure mode. Start with the minimum number of agents.
- **Explicit context passing beats implicit.** Don't assume CrewAI (or any framework) will pass context correctly — embed critical inputs directly in task descriptions.
- **Agent personas work.** Giving the LLM a specific role and backstory produces measurably better domain-specific output than a generic prompt.

### On Tool Use

- **Tools fail silently in agent systems.** When a tool returns an error string, the agent continues and fills the gap with hallucination. Tools should either succeed or raise an exception.
- **Free tools (DuckDuckGo) are sufficient for development but unreliable for production.** Budget for paid search APIs.
- **Web scraping has a fundamental ceiling.** Modern web applications render client-side; any scraping strategy that uses HTTP GET alone will fail on the sites that matter most.

### On Output Generation

- **LLM output is not safe to pass to parsers directly.** Always sanitise before passing to XML, JSON, or any structured format renderer.
- **Mixed-language pipelines (Python + Node.js) are maintenance debt.** Unify output generation in one language if possible.
- **Generate multiple output formats.** The markdown backup (`raw_outputs.md`) proved more useful than expected — users preferred reading it in a text editor over opening the PDF.

### On Development Process

- **Test with real inputs from the start.** Handwritten test fixtures mask the messiness of real-world LLM output. The hardcoded `run_analysis.py` was useful early on but masked real-world failures.
- **Version pin everything.** AI framework APIs change constantly. A pinned `requirements.txt` is not optional.
- **Instrument early.** CrewAI's `verbose=True` was the only observability tool used. A proper logging framework would have saved hours of debugging.
- **Total cost per run.** Never tracked systematically. Should have added a token counter from day one to understand cost per use.

---

*This document reflects the experience of building the Job Intelligence System v1.0.*
*Written April 2026.*

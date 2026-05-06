"""
tasks.py — Task definitions for the Job Intelligence System.

Changes from v1:
  - Task 1 (JD Analysis):  explicit step-by-step fit score template; fact-grounding
    (no inferences, only report what's explicitly in the JD/CV)
  - Task 2 (Company Research): 5 targeted searches with role_title variable;
    source attribution tags on every factual claim; location-aware salary searches
  - Task 3 (CV Tailor): stronger no-fabrication instructions; CV-anchored bullets
  - Task 4 (Interview Prep): major expansion — scenario-based technical Qs,
    seniority calibration, salary script (word-for-word), culture behavioral Qs,
    red flags section, why-this-company talking point, 30/60/90 day plan,
    tiered questions to ask per round, STAR anchoring + guidance mode

Signature:
  build_tasks(agents, jd_text, cv_text, company_name, role_title,
              experience_level, job_location)
  role_title      — extracted from JD before parallel phase (see main.py)
  experience_level — "entry" | "mid" | "senior" (extracted from JD)
  job_location    — raw location string from JD metadata (e.g. "Bengaluru, India")
"""

from crewai import Task


# ── Location classifier ───────────────────────────────────────────────────────

def _salary_config(job_location) -> dict:
    """
    Return search query, currency label, and salary table template
    appropriate for the detected job geography.

    Guardrails:
    - Accepts None / empty / non-string safely — always returns a valid dict.
    - Substring matching handles compound city names (Navi Mumbai, Greater Noida …).
    - Any unrecognised location defaults to India (primary market).
    - company/role placeholders in "search" must be filled via .format() by caller,
      with curly-brace escaping applied first.
    """
    # ── Input sanitisation ────────────────────────────────────────────────────
    if not job_location or not isinstance(job_location, str):
        job_location = ""
    # Normalise: lowercase, collapse whitespace, strip punctuation noise
    loc = " ".join(job_location.lower().split())

    # ── Geography token sets ──────────────────────────────────────────────────
    # India — primary market; includes all major metros + satellite cities
    _INDIA = {
        "india", "in",
        # Maharashtra
        "mumbai", "navi mumbai", "thane", "pune", "nagpur", "nashik",
        # Delhi NCR
        "delhi", "new delhi", "ncr", "noida", "greater noida", "gurgaon",
        "gurugram", "ghaziabad", "faridabad",
        # Karnataka
        "bangalore", "bengaluru", "blr", "mysuru", "mysore", "hubli",
        # Telangana / AP
        "hyderabad", "hyd", "secunderabad", "vizag", "visakhapatnam",
        # Tamil Nadu
        "chennai", "coimbatore", "madurai",
        # West Bengal
        "kolkata",
        # Gujarat
        "ahmedabad", "surat", "vadodara",
        # Rajasthan
        "jaipur",
        # Kerala
        "kochi", "thiruvananthapuram",
        # Punjab / Haryana / Chandigarh
        "chandigarh", "mohali", "ludhiana", "amritsar",
        # Other common shorthand
        "mum", "hyd", "blr",
    }
    _US = {
        "usa", "united states", "us",
        "new york", "nyc", "san francisco", "sf", "bay area",
        "seattle", "austin", "boston", "chicago", "los angeles", "la",
        "california", "texas", "washington", "new jersey", "virginia",
        "georgia", "atlanta", "denver", "phoenix", "miami",
    }
    _UK = {
        "uk", "united kingdom", "great britain", "england", "britain",
        "london", "manchester", "birmingham", "edinburgh", "leeds",
        "glasgow", "bristol", "cambridge", "oxford",
    }
    _SG = {"singapore", "sg"}
    _UAE = {"uae", "dubai", "abu dhabi", "sharjah"}
    _EU = {
        "germany", "france", "netherlands", "eu", "europe",
        "amsterdam", "berlin", "paris", "munich", "frankfurt",
        "dublin", "ireland", "sweden", "stockholm",
    }

    def _match(tokens):
        """True if any token appears as a substring of loc."""
        return any(t in loc for t in tokens)

    if not loc or _match(_INDIA):          # default to India (primary market)
        return {
            "search":   f'{{company}} {{role}} salary LPA india 2024 2025',
            "search_b": f'{{company}} {{role}} salary india reddit site:reddit.com',
            "search_c": f'{{company}} {{role}} salary ambitionbox naukri india',
            "currency": "INR / LPA",
            "sources":  "Reddit r/developersIndia / AmbitionBox / Naukri / Glassdoor India",
            "note":     "USD figures are NOT applicable to India roles. Use INR/LPA only. "
                        "Reddit plain-text discussions (r/developersIndia) often contain real data "
                        "even when AmbitionBox/Naukri are JS-restricted. Use snippets too.",
            "table":    (
                "| Experience Band | Base (INR LPA) | Total Comp (INR LPA) | Source |\n"
                "|-----------------|----------------|----------------------|--------|\n"
                "| 0–2 yrs (entry) | ₹X–Y LPA | ₹X–Y LPA | [source] |\n"
                "| 3–5 yrs (mid)   | ₹X–Y LPA | ₹X–Y LPA | [source] |\n"
                "| 6–9 yrs (senior)| ₹X–Y LPA | ₹X–Y LPA | [source] |\n"
                "| 10+ yrs (lead)  | ₹X–Y LPA | ₹X–Y LPA | [source] |"
            ),
        }
    elif _match(_US):
        return {
            "search":   f'{{company}} {{role}} salary total compensation 2024 2025',
            "search_b": f'{{company}} {{role}} salary reddit site:reddit.com',
            "search_c": f'{{company}} {{role}} H1B certified salary site:h1bdata.info OR site:myvisajobs.com',
            "currency": "USD",
            "sources":  "Reddit / H1B Disclosure Data / TeamBlind / Levels.fyi",
            "note":     "Figures in USD. Total comp = base + equity + bonus. "
                        "H1B disclosure data (h1bdata.info / myvisajobs.com) is plain HTML government data "
                        "with exact DOL-certified salaries — no JS, most reliable. "
                        "Reddit/TeamBlind also work. levels.fyi snippets useful.",
            "table":    (
                "| Level / Band | Base (USD) | Total Comp (USD) | Source |\n"
                "|--------------|------------|-----------------|--------|\n"
                "| Entry / L3   | $X–Y K | $X–Y K | [source] |\n"
                "| Mid / L4     | $X–Y K | $X–Y K | [source] |\n"
                "| Senior / L5  | $X–Y K | $X–Y K | [source] |\n"
                "| Staff / L6   | $X–Y K | $X–Y K | [source] |"
            ),
        }
    elif _match(_UK):
        return {
            "search":   f'{{company}} {{role}} salary uk GBP 2024 2025',
            "search_b": f'{{company}} {{role}} salary uk reddit site:reddit.com',
            "search_c": f'{{company}} {{role}} salary GBP glassdoor linkedin totaljobs',
            "currency": "GBP",
            "sources":  "Glassdoor UK / LinkedIn Salary UK / Totaljobs",
            "note":     "Figures in GBP per annum.",
            "table":    (
                "| Experience Band | Base (GBP/yr) | Total Comp (GBP/yr) | Source |\n"
                "|-----------------|---------------|---------------------|--------|\n"
                "| Entry           | £X–Y K | £X–Y K | [source] |\n"
                "| Mid             | £X–Y K | £X–Y K | [source] |\n"
                "| Senior          | £X–Y K | £X–Y K | [source] |\n"
                "| Lead            | £X–Y K | £X–Y K | [source] |"
            ),
        }
    elif _match(_SG):
        return {
            "search":   f'{{company}} {{role}} salary singapore SGD 2024 2025',
            "search_b": f'{{company}} {{role}} salary singapore reddit site:reddit.com',
            "search_c": f'{{company}} {{role}} salary SGD glassdoor tech in asia linkedin',
            "currency": "SGD",
            "sources":  "Glassdoor SG / Tech in Asia / LinkedIn Salary",
            "note":     "Figures in SGD per annum.",
            "table":    (
                "| Experience Band | Base (SGD/yr) | Total Comp (SGD/yr) | Source |\n"
                "|-----------------|---------------|---------------------|--------|\n"
                "| Entry           | S$X–Y K | S$X–Y K | [source] |\n"
                "| Mid             | S$X–Y K | S$X–Y K | [source] |\n"
                "| Senior          | S$X–Y K | S$X–Y K | [source] |\n"
                "| Lead            | S$X–Y K | S$X–Y K | [source] |"
            ),
        }
    elif _match(_UAE):
        return {
            "search":   f'{{company}} {{role}} salary dubai UAE AED 2024 2025',
            "search_b": f'{{company}} {{role}} salary UAE reddit site:reddit.com',
            "search_c": f'{{company}} {{role}} salary AED glassdoor bayt linkedin',
            "currency": "AED",
            "sources":  "Glassdoor UAE / Bayt / LinkedIn Salary",
            "note":     "Figures in AED per month unless otherwise noted.",
            "table":    (
                "| Experience Band | Monthly (AED) | Annual (AED) | Source |\n"
                "|-----------------|---------------|--------------|--------|\n"
                "| Entry           | X–Y K AED | X–Y K AED | [source] |\n"
                "| Mid             | X–Y K AED | X–Y K AED | [source] |\n"
                "| Senior          | X–Y K AED | X–Y K AED | [source] |\n"
                "| Lead            | X–Y K AED | X–Y K AED | [source] |"
            ),
        }
    elif _match(_EU):
        return {
            "search":   f'{{company}} {{role}} salary europe EUR 2024 2025',
            "search_b": f'{{company}} {{role}} salary europe reddit site:reddit.com',
            "search_c": f'{{company}} {{role}} salary EUR glassdoor linkedin levels.fyi',
            "currency": "EUR",
            "sources":  "Glassdoor EU / LinkedIn Salary / Levels.fyi EU",
            "note":     "Figures in EUR per annum (gross).",
            "table":    (
                "| Experience Band | Base (EUR/yr) | Total Comp (EUR/yr) | Source |\n"
                "|-----------------|---------------|---------------------|--------|\n"
                "| Entry           | €X–Y K | €X–Y K | [source] |\n"
                "| Mid             | €X–Y K | €X–Y K | [source] |\n"
                "| Senior          | €X–Y K | €X–Y K | [source] |\n"
                "| Lead            | €X–Y K | €X–Y K | [source] |"
            ),
        }
    else:
        # Unknown location — use local sources + flag currency
        return {
            "search":   f'{{company}} {{role}} salary compensation 2024 2025',
            "search_b": f'{{company}} {{role}} salary reddit site:reddit.com',
            "search_c": f'{{company}} {{role}} salary glassdoor linkedin payscale',
            "currency": "local currency",
            "sources":  "Glassdoor / LinkedIn Salary / PayScale",
            "note":     f"Location '{job_location}' — show figures in the local currency of the job location.",
            "table":    (
                "| Experience Band | Base | Total Comp | Source |\n"
                "|-----------------|------|-----------|--------|\n"
                "| Entry           | X–Y  | X–Y       | [source] |\n"
                "| Mid             | X–Y  | X–Y       | [source] |\n"
                "| Senior          | X–Y  | X–Y       | [source] |\n"
                "| Lead            | X–Y  | X–Y       | [source] |"
            ),
        }


# ── Shared anti-hallucination preamble (injected into every task) ─────────────

_NO_HALLUCINATION = """
ABSOLUTE RULES — NEVER VIOLATE THESE:
1. NEVER invent, guess, or extrapolate facts not present in your inputs or search results.
2. NEVER write a URL you did not receive from a search tool result. If no URL was
   returned, write "No verified URL — based on search snippet."
3. NEVER convert salaries from one currency to another — if the data is in the wrong
   currency for this role's location, mark it "Not applicable — [currency] data only."
4. NEVER fabricate metrics, dates, company names, product names, or quotes.
5. When information is absent: write "Not confirmed." Do NOT fill the gap with a
   plausible-sounding guess. A confident-sounding wrong answer is worse than "Not confirmed."
6. If a search returns no useful result for a required section, write what you searched
   for and why no result was found — do not substitute training-data knowledge.
"""


def build_tasks(
    agents:           dict,
    jd_text:          str,
    cv_text:          str,
    company_name:     str,
    role_title:       str = "this role",
    experience_level: str = "mid",
    job_location:     str = "",
) -> list:
    """
    Build and return the four tasks in execution order.
    Tasks 3 & 4 depend on Tasks 1 & 2 (passed via context=).
    """
    # ── Input guardrails ─────────────────────────────────────────────────────
    # Sanitise every string arg so downstream f-strings and .format() never crash.
    def _safe(val, default=""):
        """Return val as a stripped string, or default if None/non-string."""
        if val is None or not isinstance(val, str):
            return default
        return val.strip() or default

    company_name     = _safe(company_name,     "the company")
    role_title       = _safe(role_title,       "this role")
    experience_level = _safe(experience_level, "mid")
    job_location     = _safe(job_location,     "")

    # Escape any literal { } in user-supplied strings so .format() won't choke.
    def _esc(s):
        return s.replace("{", "{{").replace("}", "}}")

    # Resolve location-aware salary config once
    try:
        sal = _salary_config(job_location)
    except Exception:
        sal = _salary_config("")   # fallback: India defaults
    try:
        _sal_search = sal["search"].format(
            company=_esc(company_name), role=_esc(role_title)
        )
    except (KeyError, IndexError, ValueError):
        _sal_search = f"{company_name} {role_title} salary compensation 2024 2025"
    try:
        _sal_search_b = sal["search_b"].format(
            company=_esc(company_name), role=_esc(role_title)
        )
    except (KeyError, IndexError, ValueError):
        _sal_search_b = f"{company_name} {role_title} salary reddit site:reddit.com"
    try:
        _sal_search_c = sal["search_c"].format(
            company=_esc(company_name), role=_esc(role_title)
        )
    except (KeyError, IndexError, ValueError):
        _sal_search_c = f"{company_name} {role_title} salary glassdoor linkedin"
    _sal_currency = sal.get("currency", "INR / LPA")
    _sal_sources  = sal.get("sources",  "AmbitionBox / Glassdoor / Naukri")
    _sal_note     = sal.get("note",     "")
    _sal_table    = sal.get("table",    "")

    # ── Task 1: JD Analysis ──────────────────────────────────────────────────
    task_jd_analysis = Task(
        description=f"""
{_NO_HALLUCINATION}
Analyse the following Job Description against the candidate's CV.

IMPORTANT — JD FORMAT NOTE:
The JD below may be well-structured OR completely unorganized (e.g. a plain paragraph
dump, informal LinkedIn post, copy-pasted from a PDF, or missing section headers).
YOUR JOB IS TO EXTRACT REQUIREMENTS REGARDLESS OF FORMAT.
- If the JD uses bullet points → extract from bullets.
- If the JD is a paragraph → read carefully and identify implied requirements.
- If the JD is minimal (2-3 sentences) → infer requirements from job title + any skills named.
- If a section is absent (e.g. no "Preferred" section) → leave that step blank, don't fail.
Never say "the JD does not specify" as a reason to skip a section — always do your best
extraction from whatever text is present.

Ground rules: do not fabricate requirements not suggested anywhere in the JD or role title.
Do NOT use outside knowledge to add requirements the JD doesn't imply.

=== JOB DESCRIPTION ===
{jd_text}

=== CANDIDATE CV ===
{cv_text}

Your output MUST follow this exact format. Do the arithmetic explicitly before
writing any number — show every step:

## FIT SCORE CALCULATION
Step 1 — Mandatory requirements:
  List each mandatory requirement from the JD. Mark each: MET / PARTIAL / NOT MET
  Count: [X met or partial] out of [Y total mandatory]
  Mandatory score = X / Y × 50 = [calculated value]

Step 2 — Preferred/nice-to-have requirements:
  List each preferred requirement. Mark each: MET / NOT MET
  Count: [X met] out of [Y total preferred]
  Preferred score = X / Y × 30 = [calculated value]

Step 3 — Years of experience:
  JD requires: [X years]. Candidate has: [Y years relevant]. Match: FULL / PARTIAL / NONE
  Experience score: FULL = 20, PARTIAL = 10, NONE = 0 → [value]

Step 4 — Total:
  [mandatory score] + [preferred score] + [experience score] = [raw total]
  Rounded to nearest 5: [final score]

## FIT SCORE
[final score]/100 — [one sentence: biggest matching strength AND biggest gap]

## TOP JD KEYWORDS
The 10 most important keywords/skills from the JD (ATS and interviewers look for these):
- [keyword 1]
- [keyword 2]
(list exactly 10, or fewer only if the JD has fewer distinct technical skills)

## MATCHING SKILLS
Skills and experience in the CV that directly match JD requirements:
- [skill/requirement]: [exact phrase from CV that demonstrates this]
(only list items with explicit CV evidence)

## SKILL GAPS
JD requirements absent or weak in the CV:
- [missing requirement]: [Critical / Important / Nice-to-have]

## POINTS TO EMPHASISE
Specific CV items to highlight for THIS role:
- [point — be specific, quote the CV phrasing]

## ROLE SUMMARY
2-3 sentences: what this role is really about and what the employer values most.
(Base this only on the JD text. No speculation.)
""",
        expected_output=(
            "A structured JD analysis with step-by-step fit score arithmetic, "
            "top 10 keywords, matching skills (CV-evidenced), skill gaps, "
            "emphasis points, and a fact-grounded role summary."
        ),
        agent=agents["jd_analyst"],
    )

    # ── Task 2: Company Research (5 searches, clean readable output) ────────────
    task_company_research = Task(
        description=f"""
{_NO_HALLUCINATION}

Research {company_name} to help a candidate applying for the role of {role_title}
walk into the interview fully prepared.
Job location: {job_location or "Not specified — default to India salary sources."}

SEARCH STRATEGY — follow how a human researcher would find this data:
Budget: 7 mandatory searches. Run all in order. Collect EVERY salary figure visible.

── SALARY (3 dedicated searches — run ALL 3 even if the first has data) ──
Search 1 (broad):   "{_sal_search}"
Search 2 (reddit):  "{_sal_search_b}"
Search 3 (data):    "{_sal_search_c}"

SNIPPET RULE (CRITICAL): Tavily always returns a plain-text snippet per result.
Read EVERY snippet — they often contain "$X–Y K" or "₹X LPA" even for JS-heavy sites.
For US Search 3: h1bdata.info and myvisajobs.com are plain HTML government databases
with exact DOL-certified salaries — try scrape_page if a URL appears (counts as 1 step).
Record every salary number seen across all 3 searches, noting its source URL.

── COMPANY RESEARCH (4 searches) ──
Search 4 (culture):  "{company_name} culture work environment blind teamblind reviews"
Search 5 (reviews):  "{company_name} employee reviews glassdoor ambitionbox work life balance"
Search 6 (interview):"{company_name} {role_title} interview process rounds experience questions"
Search 7 (news):     "{company_name} news funding layoffs expansion hiring 2024 2025"

FALLBACK — only if all 3 salary searches return ZERO salary figures:
  Salary fallback: "{company_name} {role_title} pay compensation linkedin job posting"
  Geography rule: stay in {_sal_currency}. Never switch to USD for India/UK/SG roles.

Culture fallback (if Search 4+5 empty): "{company_name} linkedin about company culture mission values"
Interview fallback (if Search 6 empty): "{company_name} interview experience leetcode geeksforgeeks"

SCRAPING RULE: scrape at most 1 page per run (best salary URL found).

ADAPTIVE INSTRUCTIONS:
- If {company_name} is a startup / small company: Glassdoor/AmbitionBox may have no data.
  Use LinkedIn, Reddit, and news sources instead. Note "Limited public data — startup" in report.
- If {company_name} is a large MNC: AmbitionBox, Glassdoor, Blind should have solid data.
- Never fabricate data because a source returned nothing. Write "Not confirmed — no public data found."
- SALARY GEOGRAPHY: {_sal_note}
  Never convert currencies. Never guess. Mark missing data "Not confirmed."

SOURCE INTEGRITY — CRITICAL:
In the "## Sources Used" section ONLY list URLs your search tool explicitly returned.
If a search returned no URL, write: "No verified URL — based on search snippet."
Fabricating a URL is a critical failure of this task.

After completing all searches, write the full report. No more tool calls after the report begins.

OUTPUT FORMAT — CRITICAL FORMATTING RULES:
- Do NOT add a document title, header block, or any "--- # Title ---" separator.
- Start your output with the literal text "## Company Overview" on the very first line.
- Every "## Section" heading must be on its own line with a BLANK LINE before and after it.
- The FIRST character after a "## Heading" line must be a newline, NOT a dash or text.
- Each bullet point "- item" must be on its own line. NEVER write "## Heading - Item" on one line.
- WRONG:  ## Recent News - Item 1 text - Item 2 text
- CORRECT:
  ## Recent News

  - Item 1 text
  - Item 2 text

## Company Overview
[3-4 sentences: what the company does, size, HQ, founding year, market position.
Only include facts confirmed in search results.]

## Culture & Work Environment

**Overall sentiment:** [one sentence summary from reviews]

- **Work-life balance:** [rating if available, key finding from reviews]
- **Management:** [key finding — good or bad signals]
- **Career growth:** [promotion pace, learning opportunities]
- **Remote/hybrid policy:** [confirmed policy or "Not confirmed"]
- **Blind signals:** [candid anonymous themes from TeamBlind. If not found: "Not confirmed."]

## Salary — {role_title} ({_sal_currency})

> Primary source: {_sal_sources}
> {_sal_note}
> Figures marked * are estimates — verify before negotiating.
> If no data found for a band, write "Not confirmed" — do NOT guess.

{_sal_table}

If no salary data was found at all, replace the table with:
"No salary data found in searches for this role at {company_name} in {job_location or 'this location'}."

**{role_title} specific:** [figure + source, or "Not confirmed"]

**Equity / Bonus:** [joining bonus, annual bonus if found. "Not confirmed" if absent.]

## Interview Process

**Format:** [rounds + overall structure, or "Not confirmed"]

**Round breakdown:**
1. [Round name] — [what it tests]
(continue per round; write "Not confirmed" if no candidate reports found)

**Topics reported by candidates:** [specific topics from Glassdoor/Blind/AmbitionBox, or "Not confirmed"]

**Timeline:** [application to offer timeline, or "Not confirmed"]

## Pros
- [Specific pro with source detail]
- [Specific pro with source detail]
- [Specific pro with source detail]

## Cons / Red Flags
- [Specific con or red flag with source detail]
- [Specific con or red flag with source detail]

## Recent News
- [News item — what happened, when, why it matters for a candidate]
- [News item 2, or "No recent news found in searches."]

## Negotiation Intel
- **Negotiation room:** [what candidates report, or "Not confirmed"]
- **Equity negotiability:** [RSU/bonus negotiability, or "Not confirmed"]
- **Recommended approach:** [2-3 sentences grounded strictly in the data above]

## Sources Used
ONLY list URLs your search tool returned. No fabricated or training-data URLs.
- Search 1 (salary): [URL from results, or "No verified URL — based on search snippet"]
- Search 2 (culture): [URL from results, or "No verified URL — based on search snippet"]
- Search 3 (reviews): [URL from results, or "No verified URL — based on search snippet"]
- Search 4 (interview): [URL from results, or "No verified URL — based on search snippet"]
- Search 5 (news): [URL from results, or "No verified URL — based on search snippet"]
""",
        expected_output=(
            "A clean, well-structured company intelligence report. Salary table in the "
            f"correct currency ({_sal_currency}) using {_sal_sources} — wrong-currency "
            "data marked 'Not applicable' not converted. Sources section lists only URLs "
            "actually returned by search tool — never fabricated. "
            "Writes 'Not confirmed' where data is absent rather than guessing."
        ),
        agent=agents["company_researcher"],
    )

    # ── Task 3: CV Tailoring ─────────────────────────────────────────────────
    task_cv_tailoring = Task(
        description=f"""
{_NO_HALLUCINATION}
You are rewriting a CV to maximise its fit for a specific role.
Study both the JD and CV carefully, then produce a SUBSTANTIALLY REWORDED CV.

=== TARGET JOB DESCRIPTION ===
{jd_text}

=== ORIGINAL CV (factual ground truth) ===
{cv_text}

-------------------------------------------------------------
WHAT YOU MUST CHANGE (every one of these is mandatory):
-------------------------------------------------------------
1. PROFESSIONAL SUMMARY — write a brand-new 2-3 line summary using the JD's
   exact language, directly targeting {role_title} at {company_name}.
   This section almost certainly does not exist in the original — create it.

2. EVERY EXPERIENCE BULLET — reword using the JD's vocabulary and action verbs.
   Do NOT copy bullets verbatim from the original CV.
   Example: original says "built dashboards" + JD says "data visualisation pipelines"
   -> rewrite as "Engineered data visualisation pipelines and interactive dashboards..."

3. SKILLS SECTION — reorder so skills that appear in the JD come first.
   Use the exact spelling/casing from the JD (e.g. "React.js" not "ReactJS"
   if the JD writes "React.js").

4. BULLET ORDER within each role — lead with the bullet most relevant to the JD.

5. SECTION ORDER — if the JD emphasises Projects heavily, move Projects above Education.

-------------------------------------------------------------
WHAT YOU MUST NEVER CHANGE:
-------------------------------------------------------------
- Dates, company names, role titles (these are facts — do not touch)
- Do NOT invent tools, projects, skills, or achievements absent from the original
- Do NOT add metrics not already in the original (no "increased by 40%" unless
  the original already says 40%)

-------------------------------------------------------------
ACTIVE USE OF JD ANALYSIS (Task 1 context):
-------------------------------------------------------------
The JD Analysis identified specific SKILL GAPS — skills the JD requires
that are absent or weak in the original CV. For EACH gap:
  - Search the CV for any adjacent or transferable experience that partially
    addresses it (e.g. gap = "Kubernetes" → candidate has "Docker" experience).
  - If found: rewrite the relevant bullet to surface the adjacent skill
    explicitly alongside the gap technology.
    Example: "Containerised services with Docker; currently expanding to
    Kubernetes-based orchestration for production deployments."
  - If truly absent: do NOT invent it. Leave a short placeholder comment in
    the Skills section: "[ Gap: {{skill}} — not in CV, recommend upskilling ]"
    This signals the gap without fabricating a claim.

-------------------------------------------------------------
SELF-CHECK before outputting:
-------------------------------------------------------------
- Is there a Professional Summary at the top? (required)
- Are at least 70% of bullets reworded vs the original? (required)
- Does Skills lead with JD keywords? (required)
- Have ALL skill gaps from T0 been addressed (surface or flag)? (required)
- Is this the COMPLETE document, not a diff or summary? (required)

FORMAT (use ## markdown headers exactly as shown):

[CANDIDATE NAME]
[Email] | [Phone] | [LinkedIn] | [Location]

## Professional Summary
[2-3 lines targeting {role_title} at {company_name} — use JD language directly]

## Skills
[JD-relevant skills listed first, exact JD spelling/casing]

## Experience
[Company | Role | Dates — exactly as in original]
- [Reworded bullet using JD vocabulary and action verbs]
- [Reworded bullet]
...

## Education
[As in original]

## Projects / Certifications
[As in original, reordered by JD relevance if helpful]
""",
        expected_output=(
            "A complete, substantially reworded tailored CV in clean text format, "
            "ready for Word conversion. Must contain: a new Professional Summary; "
            "at least 70% of experience bullets reworded with JD vocabulary; "
            "Skills section leading with JD keywords. "
            "Zero fabricated facts — every claim traceable to the original CV."
        ),
        agent=agents["cv_tailor"],
        context=[task_jd_analysis],
    )


    return [task_jd_analysis, task_company_research, task_cv_tailoring]
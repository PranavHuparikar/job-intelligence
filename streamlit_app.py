"""
streamlit_app.py — Job Intelligence System Web UI
Run: streamlit run streamlit_app.py

Changes in this version:
  - CV file uploader (PDF / DOCX / TXT) — in addition to paste
  - JD metadata collection UI (platform, posting date, location, job type, exp level)
  - Buffered pipeline output — no LLM text shown during run (item 6)
  - Phase progress indicator (Phase 1 JD / Company / Phase 2) (item 18)
  - Source badge rendering: [source: search result] → green ✓, LLM estimate → amber ⚠
  - ATS keyword match score — computed post-run from jd_analysis + tailored_cv
  - Quality badge from evaluator (green/amber/red)
  - Similar JDs tab — BGE cosine similarity from jd_database
  - JD saved to SQLite on each run (with metadata + embedding)
  - Input sanitisation before pipeline launch

SECURITY NOTE: CV bytes are never written to disk.
"""

import io
import json
import os
import queue
import re
import sys
import threading
import time
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Optional

# ── Ensure imports resolve relative to this file ──────────────────────────────
_HERE = Path(__file__).parent.resolve()
os.chdir(_HERE)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Job Intelligence System",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Auth gate ─────────────────────────────────────────────────────────────────
# Must run before any other rendering so unauthenticated visitors see only the
# login form. Returns immediately (True) when no APP_PASSWORD is configured
# (local dev mode).
from auth import check_password
if not check_password():
    st.stop()

# ── Razorpay payment callback handler ────────────────────────────────────────
# Razorpay redirects back to APP_URL after checkout, adding payment params to
# the URL query string. We handle them on the very first page load after redirect
# (before any other UI renders) so the credit is applied immediately.
_qp = dict(st.query_params)
if "razorpay_payment_id" in _qp:
    try:
        from razorpay_handler import handle_payment_callback, razorpay_configured
        if razorpay_configured():
            _pay_result = handle_payment_callback(_qp)
            if _pay_result.get("success"):
                # Store credited email in session so the sidebar pre-fills it
                st.session_state["user_email"] = _pay_result["email"]
                st.session_state["_payment_credited"] = _pay_result
            else:
                st.session_state["_payment_error"] = _pay_result.get("error", "Unknown payment error")
    except Exception as _pay_exc:
        st.session_state["_payment_error"] = str(_pay_exc)
    # Strip the Razorpay params from the URL so refreshing doesn't re-trigger
    st.query_params.clear()

# Show payment result banners (persisted across the rerun triggered by clear)
if st.session_state.get("_payment_credited"):
    _pr = st.session_state.pop("_payment_credited")
    st.success(
        f"🎉 Payment successful! **{_pr['runs']} runs** credited to **{_pr['email']}**.  "
        f"New balance: **{_pr['balance']} runs**.",
        icon="✅",
    )
if st.session_state.get("_payment_error"):
    st.error(
        f"❌ Payment verification failed: {st.session_state.pop('_payment_error')}  \n"
        "Please contact support if you were charged.",
        icon="⚠️",
    )

# ── Startup secrets check — show a banner if keys are missing ─────────────────
def _get_secret_value(key: str) -> str:
    """Read a secret from st.secrets (cloud) or os.environ (local)."""
    try:
        val = st.secrets.get(key, "")
        if val:
            return val
    except Exception:
        pass
    return os.getenv(key, "")


def _check_secrets() -> list:
    missing = []
    if not _get_secret_value("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if not _get_secret_value("TAVILY_API_KEY"):
        missing.append("TAVILY_API_KEY")
    return missing

_missing_keys = _check_secrets()
if _missing_keys:
    st.error(
        f"⚠️ **Missing API keys:** {', '.join(_missing_keys)}  \n"
        "Add them to `.streamlit/secrets.toml` (cloud) or `.env` (local) "
        "before running an analysis. See `DEPLOY.md` for instructions.",
        icon="🔑",
    )

# ── Storage paths ─────────────────────────────────────────────────────────────
SAVED_DIR = _HERE / "saved_inputs"
CV_FILE   = SAVED_DIR / "cvs.json"
JD_FILE   = SAVED_DIR / "jds.json"
SAVED_DIR.mkdir(exist_ok=True)


# ── Simple JSON store helpers ─────────────────────────────────────────────────

def _load(path: Path) -> list:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(path: Path, items: list) -> None:
    path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def save_cv(name: str, content: str) -> None:
    items = [i for i in _load(CV_FILE) if i["name"].strip().lower() != name.strip().lower()]
    items.insert(0, {
        "id":       str(uuid.uuid4())[:8],
        "name":     name.strip(),
        "content":  content,
        "saved_at": datetime.now().strftime("%d %b %Y %H:%M"),
    })
    _save(CV_FILE, items)


def save_jd_json(name: str, company: str, content: str, metadata: dict) -> None:
    items = [i for i in _load(JD_FILE) if i["name"].strip().lower() != name.strip().lower()]
    items.insert(0, {
        "id":       str(uuid.uuid4())[:8],
        "name":     name.strip(),
        "company":  company.strip(),
        "content":  content,
        "metadata": metadata,
        "saved_at": datetime.now().strftime("%d %b %Y %H:%M"),
    })
    _save(JD_FILE, items)


def delete_cv(item_id: str) -> None:
    _save(CV_FILE, [i for i in _load(CV_FILE) if i["id"] != item_id])


def delete_jd(item_id: str) -> None:
    _save(JD_FILE, [i for i in _load(JD_FILE) if i["id"] != item_id])


# ── Markdown normalizer ───────────────────────────────────────────────────────

def _normalize_markdown(text: str) -> str:
    """
    Ensure block-level markdown elements render correctly in Streamlit.

    Problems this fixes:
    - Table separator rows |---|---|---| being destroyed by the --- regex
    - Table data rows joined on one line: "| a | b || c | d |"
    - Headings with inline content: "## Recent News - Item 1"
    - Dollar signs parsed as LaTeX: "$112K" → "\\$112K"
    - Missing blank lines before/after headings and horizontal rules
    """
    # ── 1. Normalise line endings ─────────────────────────────────────────────
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # ── 2. Strip leading document-title block ─────────────────────────────────
    #   Model sometimes prepends: "--- # Title ### subtitle ---"
    text = re.sub(
        r"^\s*-{3,}\s*#[^\n]*-{3,}\s*\n*",
        "",
        text,
        flags=re.MULTILINE,
    )

    # ── 3. Fix table rows joined on one line (line-by-line, safe) ─────────────
    # Table separator rows like |---|---| contain only |, -, :, space — skip those.
    # Data rows joined as "| a | b | | c | d |" are split at the row boundary.
    def _fix_table_line(line: str) -> str:
        stripped = line.strip()
        if not stripped.startswith("|"):
            return line
        # Skip separator rows (only |, -, :, spaces)
        if re.match(r"^[\|:\-\s]+$", stripped):
            return line
        # Split at "| |" row boundaries where second | is followed by real content
        # Pattern: | followed by optional spaces then | then optional spaces then
        # a non-|, non--, non-space character
        parts = re.split(r"\|\s*\|(?=\s*[^|\-\s])", line)
        if len(parts) > 1:
            return "|\n|".join(parts)
        return line

    lines = text.split("\n")
    text = "\n".join(_fix_table_line(ln) for ln in lines)

    # ── 4. Downgrade H1 → H2 so nothing renders as a page-title-sized heading ──
    #   Some agents use "# Title" for their first section; H1 is too large in tabs.
    text = re.sub(r"^# (?!#)", "## ", text, flags=re.MULTILINE)

    # ── 5. Ensure every heading is preceded by a blank line ───────────────────
    # Use ([^\n])\n instead of lookbehind — the lookbehind (?<!\n) incorrectly
    # matches inside '## ' (the 2nd '#' is not preceded by \n, so it inserts
    # '\n\n' mid-heading, turning '## Title' into '#\n\n# Title').
    text = re.sub(r"([^\n])\n(#{1,6} )", r"\1\n\n\2", text)

    # ── 6. Ensure a blank line after every heading ────────────────────────────
    text = re.sub(r"^(#{1,6}[^\n]+)$", r"\1\n", text, flags=re.MULTILINE)

    # ── 7. Horizontal rules: blank lines around standalone --- (not table rows) ─
    # Process line-by-line so we NEVER touch |---|---| separator rows.
    lines = text.split("\n")
    result: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        is_standalone_hr = re.match(r"^-{3,}$", stripped) and "|" not in ln
        if is_standalone_hr:
            if result and result[-1].strip():
                result.append("")
            result.append(ln)
            # blank line after will be added naturally when next line is appended
        else:
            result.append(ln)
    text = "\n".join(result)

    # ── 8. Escape bare $ signs so Streamlit doesn't render them as LaTeX ──────
    #   "$112K" → "\\$112K"  (Streamlit uses $...$ for inline math / KaTeX)
    text = re.sub(r"\$(?=[\d\w])", r"\\$", text)

    # ── 9. Collapse 3+ blank lines → 2 ───────────────────────────────────────
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ── Source badge rendering ────────────────────────────────────────────────────

def render_source_badges(text: str) -> str:
    """
    Cleanup pass on company research output:
    1. Normalise markdown block elements so headers render correctly.
    2. Split any "## Section - inline content" patterns (company intel specific).
    3. Remove any inline [source:...] tags that slipped through.
    """
    text = _normalize_markdown(text)

    # Company intel often emits "## Recent News - Item text" on one line.
    # Split it so the heading and content render as separate elements.
    def _split_heading_content(m: re.Match) -> str:
        line = m.group(0)
        idx = line.find(" - ")
        if idx != -1 and len(line) - idx > 8:
            heading_part = line[:idx].rstrip()
            content_part = line[idx + 3:].strip()
            if content_part:
                return f"{heading_part}\n\n- {content_part}"
        return line
    text = re.sub(r"^#{1,6} [^\n]+$", _split_heading_content, text, flags=re.MULTILINE)
    # Re-collapse any triple blank lines the split may have introduced
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove stray inline source annotations
    text = re.sub(r"\[source:[^\]]*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[single\s+source[^\]]*\]", "*(verify)*", text, flags=re.IGNORECASE)
    # Collapse multiple spaces (but not newlines) left after tag removal
    text = re.sub(r"(?<!\n) {2,}", " ", text)
    return text.strip()


# ── ATS keyword match ─────────────────────────────────────────────────────────
from jd_utils import compute_ats_score, preprocess_jd  # (found_n, total_n, found_kws, missing_kws)


# ── Pipeline worker (buffered, no LLM output to UI) ──────────────────────────

def _pipeline_worker(
    cv_text:          str,
    jd_text:          str,
    company_name:     str,
    model:            str,
    role_title:       str,
    experience_level: str,
    jd_metadata:      dict,
    q:                queue.Queue,
    result_holder:    list,
    force_fresh:      bool = False,
) -> None:
    """
    Runs in a background thread.
    Sends only structured progress messages through q (not LLM output).
    LLM verbose output goes to the terminal (or /dev/null on prod).
    result_holder[0] is set to the result dict at the end.

    SECURITY: CV text is only in memory — never written to a log.
    """
    def progress(msg: str) -> None:
        q.put({"type": "progress", "msg": msg})

    # ── System log: register run ──────────────────────────────────────────────
    run_id    = None
    run_start = time.time()
    _end_run  = None   # assigned below — guards all call sites against ImportError
    try:
        from system_log import start_run, log_event, end_run as _end_run
        run_id = start_run(
            company=company_name,
            model=model,
            experience_level=experience_level,
            source="streamlit",
        )
    except Exception:
        pass  # logging failure must never block the pipeline

    try:
        # crewai.telemetry imports pkg_resources which uv doesn't expose.
        # On failure, add a minimal shim and retry — Python removes the
        # partially-imported module from sys.modules on failure, so the
        # retry re-runs the import cleanly with the shim in place.
        try:
            from crewai import Crew, Process
        except ModuleNotFoundError as _e:
            if 'pkg_resources' not in str(_e):
                raise
            import types as _t
            _m = _t.ModuleType('pkg_resources')
            _m.require            = lambda *a, **k: None
            _m.get_distribution   = lambda n: type('D', (), {'version': '0.0.0'})()
            _m.DistributionNotFound = Exception
            _m.VersionConflict    = Exception
            _m.working_set        = []
            sys.modules['pkg_resources'] = _m
            del _t, _m, _e
            from crewai import Crew, Process
        from agents import build_agents
        from tasks import build_tasks
        from sanitizer import sanitize_cv_and_jd
        from output_generator import generate_cv_docx, generate_report_pdf
        from jd_database import save_jd, find_similar_jds, get_jd_count

        # ── Sanitize inputs ───────────────────────────────────────────────────
        clean_cv, clean_jd, san_warnings = sanitize_cv_and_jd(cv_text, jd_text)
        for w in san_warnings:
            progress(f"⚠ Security: {w}")

        # ── Authenticity check (heuristics + Haiku) ───────────────────────────
        progress("Validating documents…")
        try:
            from sanitizer import validate_document_authenticity
            auth_errors, auth_warnings = validate_document_authenticity(
                clean_cv, clean_jd, use_llm=False
            )
            if auth_errors:
                result_holder[0] = {
                    "success": False,
                    "error": " | ".join(auth_errors),
                }
                return
            for w in auth_warnings:
                progress(f"⚠ {w}")
        except Exception:
            pass  # validation failure must never block the pipeline

        # ── JD analysis cache lookup ──────────────────────────────────────────
        _jd_cache = None
        if not force_fresh:
            try:
                from jd_database import get_cached_analysis as _get_cache
                _jd_cache = _get_cache(clean_jd, company_name)
            except Exception:
                pass

        if _jd_cache:
            progress("⚡ JD analysis cached — skipping Phase 1 agents")
        elif force_fresh:
            progress("🔄 Force fresh — bypassing cache")

        # ── Company research cache lookup ─────────────────────────────────────
        _company_cache = None
        if not force_fresh and not _jd_cache:
            # Only check company cache when JD cache missed — if JD cache hit,
            # T1 is already injected from the JD cache bundle.
            try:
                from company_cache import get_cached_research as _get_company_cache
                _company_cache = _get_company_cache(company_name)
                if _company_cache:
                    progress("⚡ Company research cached — skipping T1")
            except Exception:
                _company_cache = None

        # ── Build pipeline ────────────────────────────────────────────────────
        agents = build_agents(model)
        tasks  = build_tasks(
            agents, clean_jd, clean_cv, company_name,
            role_title=role_title,
            experience_level=experience_level,
            job_location=jd_metadata.get("location", ""),
        )

        errors = {}

        # ── Dependency-graph parallel execution ───────────────────────────────
        # Dependency graph:
        #   T0 (JD Analysis)       — no deps
        #   T1 (Company Research)  — no deps
        #   T2 (CV Tailor)         — needs T0 only  ← starts as soon as T0 done
        #
        # Key optimisation: T2 no longer waits for T1 (company research).
        # T1 tail overlaps with T2, saving ~30-60 s per run.

        t0_done = threading.Event()
        t1_done = threading.Event()
        # Captures full task output BEFORE events fire — prevents race where
        # another thread could mutate tasks[idx].output.raw before the
        # monitor thread reads it for user display.
        _saved_outputs: dict = {}

        if run_id:
            try: log_event(run_id, "phase1", "start")
            except Exception: pass
        progress("PHASE:1:jd:running")
        progress("PHASE:1:company:running")

        # If cache hit, pre-inject T0/T1 outputs and signal their events now
        if _jd_cache:
            _cached_jd_analysis, _cached_company_research = _jd_cache
            try:
                from crewai.tasks.task_output import TaskOutput
                tasks[0].output = TaskOutput(
                    description="(cached)", raw=_cached_jd_analysis, agent="jd_analyst"
                )
                tasks[1].output = TaskOutput(
                    description="(cached)", raw=_cached_company_research,
                    agent="company_researcher"
                )
                # Populate _saved_outputs so the monitor thread can send partial_results
                # even though no task threads run for T0/T1 on a cache hit.
                _saved_outputs[0] = _cached_jd_analysis
                _saved_outputs[1] = _cached_company_research
                t0_done.set()
                t1_done.set()
                progress("PHASE:1:jd:done")
                progress("PHASE:1:company:done")
            except Exception:
                _jd_cache = None  # injection failed — fall back to running T0/T1
        elif _company_cache:
            # JD cache missed but company research is cached — inject T1 only
            try:
                from crewai.tasks.task_output import TaskOutput
                tasks[1].output = TaskOutput(
                    description="(cached)", raw=_company_cache, agent="company_researcher"
                )
                # Populate _saved_outputs so monitor thread can send partial_result for T1
                _saved_outputs[1] = _company_cache
                t1_done.set()
                progress("PHASE:1:company:done")
            except Exception:
                _company_cache = None  # injection failed — T1 will run normally

        _RETRY_DELAYS = [5, 15, 45]  # seconds between retries on rate-limit errors

        def _run_task(agent_key: str, idx: int,
                      wait_for: list = None,
                      signals: list = None,
                      pre_hook=None) -> None:
            """
            Run one Crew task with:
              - event-based dependency waiting
              - exponential-backoff retry on Anthropic 429 / rate-limit errors
              - optional pre_hook() called after deps are met (used for context trimming)
            """
            try:
                if wait_for:
                    for ev in wait_for:
                        ev.wait()
                if errors:
                    return
                if pre_hook:
                    pre_hook()

                last_exc = None
                for attempt in range(len(_RETRY_DELAYS) + 1):
                    try:
                        Crew(
                            agents=[agents[agent_key]],
                            tasks=[tasks[idx]],
                            process=Process.sequential,
                            verbose=True,
                        ).kickoff()
                        break  # success
                    except Exception as exc:
                        last_exc = exc
                        err_str = str(exc).lower()
                        is_rate_limit = any(
                            x in err_str for x in
                            ["rate limit", "429", "ratelimit", "too many requests",
                             "overloaded", "quota"]
                        )
                        if is_rate_limit and attempt < len(_RETRY_DELAYS):
                            wait_secs = _RETRY_DELAYS[attempt]
                            progress(
                                f"⟳ Rate limit on {agent_key} — "
                                f"retrying in {wait_secs}s (attempt {attempt + 2}/4)…"
                            )
                            time.sleep(wait_secs)
                        else:
                            raise
                if last_exc and not tasks[idx].output:
                    raise last_exc

            except Exception as e:
                errors[agent_key] = e
            finally:
                # Snapshot full raw output before signalling — the T3 pre_hook
                # trims tasks[idx].output.raw in the T3 thread the instant
                # these events fire, racing with the monitor thread's display save.
                if tasks[idx].output and tasks[idx].output.raw:
                    _saved_outputs[idx] = tasks[idx].output.raw
                if signals:
                    for ev in signals:
                        ev.set()

        # Build thread list — skip T0/T1 if cache was injected successfully
        _threads = []
        if not _jd_cache:
            _threads.append(threading.Thread(
                target=_run_task,
                kwargs={"agent_key": "jd_analyst", "idx": 0,
                        "wait_for": [],            "signals": [t0_done]},
                name="JD-Analyst",
            ))
            if not _company_cache:
                # Only run T1 if company cache also missed
                _threads.append(threading.Thread(
                    target=_run_task,
                    kwargs={"agent_key": "company_researcher", "idx": 1,
                            "wait_for": [],                    "signals": [t1_done]},
                    name="Company-Researcher",
                ))
        _threads += [
            threading.Thread(
                target=_run_task,
                kwargs={"agent_key": "cv_tailor",         "idx": 2,
                        "wait_for": [t0_done],  "signals": []},
                name="CV-Tailor",
            ),
        ]
        for t in _threads:
            t.start()

        # Progress: watch events as they fire (this runs in the worker thread)
        t0_done.wait()
        progress("PHASE:1:jd:done")
        if run_id:
            try: log_event(run_id, "phase1_jd", "done")
            except Exception: pass
        # Save FULL output now before any mutation
        _saved_jd_analysis = _saved_outputs.get(0) if not errors else None
        if _saved_jd_analysis:
            q.put({"type": "partial_result", "key": "jd_analysis",
                   "value": _saved_jd_analysis})
        progress("PHASE:2:cv:running")   # CV tailor already running

        t1_done.wait()
        progress("PHASE:1:company:done")
        if run_id:
            try: log_event(run_id, "phase1_company", "done")
            except Exception: pass
        # Save FULL output now — trim runs later
        _saved_company_research = _saved_outputs.get(1) if not errors else None
        if _saved_company_research:
            q.put({"type": "partial_result", "key": "company_research",
                   "value": _saved_company_research})
            # Cache company research for future runs (best-effort, 24h TTL)
            if not _company_cache and not _jd_cache:
                try:
                    from company_cache import cache_research as _cache_company
                    _cache_company(company_name, _saved_outputs.get(1) or _saved_company_research)
                except Exception:
                    pass

        for t in _threads:
            t.join()
        progress("PHASE:2:cv:done")
        progress("PHASE:2:done")
        _t2_full = _saved_outputs.get(2)
        if not errors and _t2_full:
            q.put({"type": "partial_result", "key": "tailored_cv",
                   "value": _t2_full})
        if run_id:
            try: log_event(run_id, "phase2_cv", "done")
            except Exception: pass
            try: log_event(run_id, "phase2", "done")
            except Exception: pass

        if errors:
            key, exc = next(iter(errors.items()))
            if run_id:
                try: log_event(run_id, "phase1", "error", detail=type(exc).__name__)
                except Exception: pass
            result_holder[0] = {"success": False, "error": f"{key}: {exc}"}
            return

        for idx, label in [(0, "JD Analysis"), (1, "Company Research"),
                           (2, "CV Tailoring")]:
            if not tasks[idx].output:
                result_holder[0] = {"success": False, "error": f"{label} produced no output."}
                return

        # Use pre-trim snapshots so the user sees the FULL JD analysis and
        # company research — not the versions trimmed for T3 context efficiency.
        jd_analysis      = _saved_jd_analysis      or tasks[0].output.raw
        company_research = _saved_company_research or tasks[1].output.raw
        tailored_cv      = tasks[2].output.raw

        # ── Store analysis to cache (only for fresh runs, not cache hits) ─────
        if not _jd_cache:
            try:
                from jd_database import store_analysis as _store_analysis
                # jd_id is set below — store after save_jd returns it
            except Exception:
                pass

        # ── Validate pipeline outputs ─────────────────────────────────────────
        try:
            from sanitizer import validate_pipeline_outputs
            _out_issues = validate_pipeline_outputs(
                jd_analysis, company_research, tailored_cv
            )
            if _out_issues:
                for issue in _out_issues:
                    progress(f"⚠ Output warning: {issue}")
        except Exception:
            pass

        # ── Save JD to database ───────────────────────────────────────────────
        progress("Saving JD to database…")
        try:
            jd_id = save_jd(
                jd_text=clean_jd,
                title=role_title,
                company=company_name,
                location=jd_metadata.get("location", ""),
                platform=jd_metadata.get("platform", ""),
                posting_date=jd_metadata.get("posting_date", ""),
                job_type=jd_metadata.get("job_type", ""),
                experience_level=experience_level,
            )
        except Exception as e:
            jd_id = None
            progress(f"⚠ JD database save failed: {e}")

        # Store T0/T1 outputs to cache for future identical JD submissions
        if jd_id and not _jd_cache:
            try:
                from jd_database import store_analysis as _store_analysis
                _store_analysis(jd_id, jd_analysis, company_research)
            except Exception:
                pass

        # ── Find similar JDs ──────────────────────────────────────────────────
        similar_jds = []
        try:
            if get_jd_count() > 1:
                similar_jds = find_similar_jds(
                    jd_text=clean_jd,
                    top_k=5,
                    exclude_id=jd_id,
                    experience_level=experience_level,
                    job_type=jd_metadata.get("job_type", ""),
                )
        except Exception as e:
            progress(f"⚠ Similarity search failed: {e}")

        # ── Generate output files ─────────────────────────────────────────────
        safe = "".join(
            c if c.isalnum() or c in "._-" else "_" for c in company_name
        ).strip("_")
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = _HERE / "outputs" / f"{safe}_{ts}"
        out.mkdir(parents=True, exist_ok=True)

        cv_path  = str(out / "tailored_cv.docx")
        pdf_path = str(out / "job_intelligence.pdf")
        md_path  = out / "raw_outputs.md"

        # Generate .docx and .pdf in parallel (independent operations)
        import concurrent.futures as _cf
        def _gen_pdf():
            generate_report_pdf(
                company_name=company_name,
                jd_analysis=jd_analysis,
                company_research=company_research,
                output_path=pdf_path,
            )
        with _cf.ThreadPoolExecutor(max_workers=2) as _pool:
            _f1 = _pool.submit(generate_cv_docx, tailored_cv, cv_path)
            _f2 = _pool.submit(_gen_pdf)
            _f1.result()
            _f2.result()
        md_path.write_text(
            f"# JD Analysis\n\n{jd_analysis}\n\n"
            f"# Company Intelligence\n\n{company_research}\n\n"
            f"# Tailored CV\n\n{tailored_cv}\n",
            encoding="utf-8",
        )

        # ── Prune old output directories (keep newest 20) ────────────────────
        try:
            _outputs_root = _HERE / "outputs"
            if _outputs_root.exists():
                _dirs = sorted(
                    [d for d in _outputs_root.iterdir() if d.is_dir()],
                    key=lambda d: d.stat().st_mtime,
                    reverse=True,
                )
                import shutil as _shutil
                for _old_dir in _dirs[20:]:
                    _shutil.rmtree(_old_dir, ignore_errors=True)
        except Exception:
            pass  # cleanup failure must never block the pipeline

        # ── Quality evaluation ────────────────────────────────────────────────
        progress("Running quality evaluation…")
        quality_report = {}
        try:
            from evaluator import evaluate_pipeline_output
            quality_report = evaluate_pipeline_output(
                cv_text=clean_cv,
                jd_text=clean_jd,
                jd_analysis=jd_analysis,
                tailored_cv=tailored_cv,
                output_dir=str(out),
            )
        except Exception as e:
            quality_report = {"overall_quality": "unknown", "error": str(e)}

        ats_str = None
        try:
            from jd_utils import compute_ats_score as _ats
            fn, tn, _, _ = _ats(jd_analysis, tailored_cv)
            if tn > 0:
                ats_str = f"{fn}/{tn}"
        except Exception:
            pass

        # ── System log: run complete ──────────────────────────────────────────
        if run_id and _end_run:
            try:
                _end_run(
                    run_id=run_id,
                    success=True,
                    duration_secs=time.time() - run_start,
                    model=model,
                    quality=quality_report.get("overall_quality"),
                    ats_score=ats_str,
                )
            except Exception:
                pass

        result_holder[0] = {
            "success":         True,
            "company_name":    company_name,   # snapshot at run-time, not live session state
            "jd_analysis":     jd_analysis,
            "company_research":company_research,
            "tailored_cv":     tailored_cv,

            "cv_path":         cv_path,
            "pdf_path":        pdf_path,
            "md_path":         str(md_path),
            "quality_report":  quality_report,
            "similar_jds":     similar_jds,
        }

    except Exception as e:
        import traceback
        if run_id and _end_run:
            try:
                _end_run(
                    run_id=run_id,
                    success=False,
                    duration_secs=time.time() - run_start,
                    model=model,
                    error_type=type(e).__name__,
                )
            except Exception:
                pass
        result_holder[0] = {
            "success": False,
            "error":   f"{type(e).__name__}: {e}",
            "detail":  traceback.format_exc(),
        }
    finally:
        q.put({"type": "done"})


def run_pipeline(
    cv_text:          str,
    jd_text:          str,
    company_name:     str,
    model:            str,
    role_title:       str,
    experience_level: str,
    jd_metadata:      dict,
    progress_placeholder,
    section_placeholders: dict = None,
    force_fresh:      bool = False,
) -> dict:
    """
    Launch pipeline in background thread.
    Updates progress_placeholder with phase status grid.
    Fills section_placeholders[key] with full markdown as each agent finishes.
    Blocks until complete, then returns result dict.
    """
    q             = queue.Queue()
    result_holder = [None]

    worker = threading.Thread(
        target=_pipeline_worker,
        args=(
            cv_text, jd_text, company_name, model,
            role_title, experience_level, jd_metadata,
            q, result_holder, force_fresh,
        ),
        daemon=True,
    )
    worker.start()

    # Phase state machine
    phase_state = {
        "jd":        "waiting",   # waiting | running | done
        "company":   "waiting",
        "cv_tailor": "waiting",
        "interview": "waiting",
        "msgs":      [],
    }

    _ICONS = {"waiting": "○", "running": "⟳", "done": "✓"}
    _COLS  = {"waiting": "#888", "running": "#0072C6", "done": "#1a7f1a"}

    def _render_phase() -> str:
        def cell(label: str, state: str) -> str:
            icon  = _ICONS[state]
            color = _COLS[state]
            return (
                f'<span style="color:{color};font-weight:600;margin-right:20px">'
                f'{icon} {label}</span>'
            )
        row1 = (
            cell("JD Analysis",      phase_state["jd"])
            + cell("Company Research", phase_state["company"])
        )
        row2 = (
            cell("CV Tailoring",   phase_state["cv_tailor"])

        )
        msgs_html = ""
        for m in phase_state["msgs"][-4:]:
            msgs_html += f'<div style="color:#888;font-size:0.8em">{m}</div>'
        return (
            '<div style="padding:12px;background:#f8f9fb;border-radius:6px;'
            'font-family:monospace;margin-bottom:8px">'
            f'<div style="margin-bottom:6px">{row1}</div>'
            f'<div>{row2}</div>'
            + "</div>"
            + msgs_html
        )

    progress_placeholder.markdown(_render_phase(), unsafe_allow_html=True)

    while True:
        try:
            item = q.get(timeout=0.3)
            if item["type"] == "done":
                break
            if item["type"] == "partial_result":
                key = item["key"]
                if section_placeholders and key in section_placeholders:
                    val = item["value"]
                    if key == "company_research":
                        val = render_source_badges(val)
                    else:
                        val = _normalize_markdown(val)
                    section_placeholders[key].markdown(val, unsafe_allow_html=True)
            elif item["type"] == "progress":
                msg = item["msg"]
                if msg == "PHASE:1:jd:running":
                    phase_state["jd"] = "running"
                elif msg == "PHASE:1:company:running":
                    phase_state["company"] = "running"
                elif msg == "PHASE:1:jd:done":
                    phase_state["jd"] = "done"
                elif msg == "PHASE:1:company:done":
                    phase_state["company"] = "done"
                elif msg == "PHASE:2:cv:running":
                    phase_state["cv_tailor"] = "running"
                elif msg == "PHASE:2:cv:done":
                    phase_state["cv_tailor"] = "done"

                elif msg == "PHASE:2:done":
                    phase_state["cv_tailor"] = "done"

                else:
                    phase_state["msgs"].append(msg)
                progress_placeholder.markdown(_render_phase(), unsafe_allow_html=True)
        except queue.Empty:
            pass

    worker.join()

    # Final state: only mark all ✓ if the run actually succeeded.
    # On error, leave the phase indicators where they stopped so the user
    # can see which agent was in progress when the failure occurred.
    result = result_holder[0]
    if result is None:
        # Worker thread crashed before setting result_holder — surface a clear error
        result = {
            "success": False,
            "error": (
                "Pipeline thread terminated unexpectedly. "
                "Check server logs for the full traceback."
            ),
        }
    if result.get("success"):
        for k in ("jd", "company", "cv_tailor"):
            phase_state[k] = "done"
        progress_placeholder.markdown(_render_phase(), unsafe_allow_html=True)

    return result


# ── Session state defaults ────────────────────────────────────────────────────
for key, default in {
    "cv_area":        "",
    "jd_area":        "",
    "company_input":  "",
    "run_result":     None,
    "running":        False,
    "cv_bytes":       None,   # uploaded CV file bytes
    "cv_filename":    "",     # uploaded CV filename
    "_cv_upload_id":  "",     # tracks last processed upload to avoid re-reading on every rerun
    "last_run_at":    0,      # epoch seconds of last successful run start (rate limiting)
    "_fresh_key_ver": 0,      # incremented after run to reset the Fresh toggle to off
    "user_email":     "",     # email used for credits / payment
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.title("🎯 Job Intelligence")
    st.caption("Powered by Claude + CrewAI")

    # ── Credits & email ───────────────────────────────────────────────────────
    try:
        from credits import credits_configured, get_credits, BUNDLES
        from razorpay_handler import razorpay_configured, create_payment_link

        _credits_on  = credits_configured()
        _razorpay_on = razorpay_configured()

        if _credits_on:
            st.subheader("👤 Your Account")

            # ── User identity (Google OAuth or manual email) ──────────────────
            _auth_user = st.session_state.get("_auth_user")
            if _auth_user:
                # Google-authenticated user — show name + avatar, lock email
                _gname   = _auth_user.get("name", "")
                _gavatar = _auth_user.get("avatar", "")
                _gemail  = _auth_user.get("email", "")
                if _gavatar:
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">'
                        f'<img src="{_gavatar}" width="36" height="36" '
                        f'style="border-radius:50%;border:1px solid #ddd">'
                        f'<div><strong>{_gname}</strong><br>'
                        f'<span style="font-size:0.82em;color:#888">{_gemail}</span></div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(f"**{_gname or _gemail}**")
                    if _gname:
                        st.caption(_gemail)
                # Sync email for credits
                st.session_state["user_email"] = _gemail

                # Sign-out button
                if st.button("Sign out", key="_signout_btn", use_container_width=True):
                    from auth import sign_out
                    sign_out()
            else:
                # No Google auth — manual email entry (legacy / local dev)
                _email_input = st.text_input(
                    "Email address",
                    value=st.session_state.get("user_email", ""),
                    placeholder="you@example.com",
                    key="user_email_input",
                    help="Used to track your run credits.",
                )
                if _email_input.strip() != st.session_state.get("user_email", ""):
                    st.session_state["user_email"] = _email_input.strip()

            _email = st.session_state.get("user_email", "").strip()

            if _email:
                _bal = get_credits(_email)
                _bal_color = "#1a7f1a" if _bal > 0 else "#b81a1a"
                st.markdown(
                    f'<p style="font-size:0.9em">Credits remaining: '
                    f'<strong style="color:{_bal_color}">{_bal}</strong></p>',
                    unsafe_allow_html=True,
                )

                # ── Buy Credits ───────────────────────────────────────────────
                if _razorpay_on:
                    with st.expander("💳 Buy Credits"):
                        for _bkey, _bundle in BUNDLES.items():
                            _col_label, _col_btn = st.columns([3, 2])
                            with _col_label:
                                st.markdown(
                                    f"**{_bundle['label']}**  \n"
                                    f"{_bundle['price_display']}",
                                    unsafe_allow_html=False,
                                )
                            with _col_btn:
                                if st.button(
                                    "Buy",
                                    key=f"buy_{_bkey}",
                                    use_container_width=True,
                                ):
                                    try:
                                        _url = create_payment_link(_email, _bkey)
                                        st.markdown(
                                            f'<a href="{_url}" target="_blank">'
                                            f'<button style="width:100%;padding:6px;'
                                            f'background:#6f42c1;color:white;border:none;'
                                            f'border-radius:4px;cursor:pointer">'
                                            f'Checkout →</button></a>',
                                            unsafe_allow_html=True,
                                        )
                                    except Exception as _pe:
                                        st.error(f"Could not create payment link: {_pe}")
            else:
                st.caption("Enter your email to see your credit balance.")

            st.divider()

    except Exception as _cred_exc:
        # Credits module not installed or misconfigured — degrade silently
        pass

    # ── Saved CVs ─────────────────────────────────────────────────────────────
    st.subheader("📄 Saved CVs")
    cvs = _load(CV_FILE)
    if cvs:
        for cv in cvs:
            c1, c2 = st.columns([5, 1])
            with c1:
                if st.button(f"📋 {cv['name']}", key=f"lcv_{cv['id']}", use_container_width=True):
                    st.session_state["cv_area"]     = cv["content"]
                    st.session_state["cv_bytes"]    = None
                    st.session_state["cv_filename"] = ""
                    st.rerun()
            with c2:
                if st.button("✕", key=f"dcv_{cv['id']}"):
                    delete_cv(cv["id"]); st.rerun()
    else:
        st.caption("No saved CVs yet.")

    with st.expander("💾 Save current CV as…"):
        cv_name = st.text_input("Name", placeholder="Master CV — May 2026", key="cv_save_name")
        if st.button("Save CV", use_container_width=True, key="btn_save_cv"):
            content = st.session_state.get("cv_area", "").strip()
            if cv_name and content:
                save_cv(cv_name, content)
                st.success(f"Saved: {cv_name}"); st.rerun()
            else:
                st.warning("Enter a name and make sure the CV field isn't empty.")

    st.divider()

    # ── Saved JDs ─────────────────────────────────────────────────────────────
    st.subheader("💼 Saved JDs")
    jds = _load(JD_FILE)
    if jds:
        for jd in jds:
            c1, c2 = st.columns([5, 1])
            with c1:
                if st.button(f"🏢 {jd['name']}", key=f"ljd_{jd['id']}", use_container_width=True):
                    st.session_state["_jd_area_pending"] = jd["content"]
                    st.session_state["company_input"] = jd.get("company", "")
                    st.rerun()
            with c2:
                if st.button("✕", key=f"djd_{jd['id']}"):
                    delete_jd(jd["id"]); st.rerun()
    else:
        st.caption("No saved JDs yet.")

    with st.expander("💾 Save current JD as…"):
        jd_name = st.text_input("Name", placeholder="NTT DATA — GenAI Engineer", key="jd_save_name")
        if st.button("Save JD", use_container_width=True, key="btn_save_jd"):
            jd_content  = st.session_state.get("jd_area", "").strip()
            company_val = st.session_state.get("company_input", "").strip()
            if jd_name and jd_content:
                save_jd_json(jd_name, company_val, jd_content, {})
                st.success(f"Saved: {jd_name}"); st.rerun()
            else:
                st.warning("Enter a name and make sure the JD field isn't empty.")

    # ── System stats panel (admin only) ────────────────────────────────────────
    st.divider()
    if os.getenv("ADMIN_MODE", "").lower() == "true" or _get_secret_value("ADMIN_MODE").lower() == "true":
     try:
        from jd_database import _supabase_configured, get_jd_count
        from system_log import get_active_count, get_active_runs, get_today_stats

        # Supabase connection status (admin only)
        try:
            if _supabase_configured():
                jd_n = get_jd_count()
                st.caption(f"🟢 Supabase · {jd_n} JD{'s' if jd_n != 1 else ''} stored")
            else:
                _missing_sb = [k for k in ("SUPABASE_URL", "SUPABASE_KEY", "VOYAGE_API_KEY") if not _get_secret_value(k)]
                st.caption(f"🔴 Supabase missing: {', '.join(_missing_sb)}" if _missing_sb else "🔴 Supabase not configured")
        except Exception:
            pass

        jd_count     = get_jd_count()
        active_count = get_active_count()
        stats        = get_today_stats()

        st.caption("**📊 System**")

        # Active runs indicator
        if active_count > 0:
            active_label = (
                f"🟢 {active_count} run{'s' if active_count > 1 else ''} active"
            )
            st.markdown(
                f'<span style="color:#1a7f1a;font-size:0.82em;font-weight:600">'
                f'{active_label}</span>',
                unsafe_allow_html=True,
            )
            for r in get_active_runs():
                elapsed = r["elapsed_secs"]
                m, s = divmod(elapsed, 60)
                st.caption(
                    f"  · {r['company']} ({r['model'].split('-')[1] if '-' in r['model'] else r['model']}) "
                    f"— {m}m {s}s"
                )
        else:
            st.caption("○ No runs active")

        # Today's stats
        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("Today's runs", stats["runs_total"])
        with col_b:
            st.metric("Est. cost", f"${stats['daily_cost_usd']:.2f}")

        if stats["runs_total"] > 0:
            avg = stats["avg_duration_secs"]
            m, s = divmod(int(avg), 60)
            q_counts = stats["quality_counts"]
            st.caption(
                f"Avg run: {m}m {s}s  ·  "
                f"✓{q_counts.get('green',0)} "
                f"⚠{q_counts.get('amber',0)} "
                f"✗{q_counts.get('red',0)}"
            )

        st.caption(f"🗄 JD database: {jd_count} JD{'s' if jd_count != 1 else ''}")

     except Exception:
          try:
              from jd_database import get_jd_count
              st.caption(f"🗄 JD database: {get_jd_count()} JDs")
          except Exception:
              pass


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN AREA
# ══════════════════════════════════════════════════════════════════════════════

st.title("🎯 Job Intelligence System")
st.caption("JD analysis · Company intel · Tailored CV · Interview prep — all in one run.")

# ── CV upload callback — fires ONLY when a new file is selected ───────────────
# Using on_change is the correct Streamlit pattern: the callback runs before the
# next render, so cv_area is always populated when the text_area renders.
# Inline session-state mutation (the old approach) is unreliable in Streamlit
# 1.35+ and caused the "CV is empty" symptom.

def _on_cv_upload():
    uploaded = st.session_state.get("cv_upload")
    if uploaded is None:
        return
    try:
        from cv_utils import read_cv_bytes
        extracted = read_cv_bytes(uploaded.read(), uploaded.name)
        st.session_state["cv_area"]      = extracted
        st.session_state["cv_filename"]  = uploaded.name
        st.session_state["_cv_upload_id"] = f"{uploaded.name}:{uploaded.size}"
    except Exception as e:
        st.session_state["_cv_upload_error"] = str(e)


# ── CV input — file uploader + text area ──────────────────────────────────────
col_cv, col_jd = st.columns(2)

with col_cv:
    st.subheader("Your CV")
    st.file_uploader(
        "Upload CV (PDF, DOCX, or TXT)",
        type=["pdf", "docx", "txt"],
        key="cv_upload",
        on_change=_on_cv_upload,
        help="Upload your CV file — it will be read into the field below.",
    )
    if st.session_state.get("_cv_upload_error"):
        st.error(f"Could not read CV file: {st.session_state.pop('_cv_upload_error')}")

    st.text_area(
        "CV",
        height=280,
        placeholder="Or paste CV text here, or load from sidebar →",
        label_visibility="collapsed",
        key="cv_area",
    )
    wc = len(st.session_state["cv_area"].split()) if st.session_state["cv_area"].strip() else 0
    if st.session_state.get("cv_filename"):
        st.caption(f"📎 {st.session_state['cv_filename']} · {wc} words")
    elif wc:
        st.caption(f"{wc} words")
    if wc > 1600:
        st.warning(f"⚠ {wc} words — trim to <1600 for best ATS performance.")
    st.caption("🔒 Your CV is never stored or logged.")

# ── Transfer staged JD load BEFORE any widgets are instantiated ──────────────
# All widget-bound keys are transferred here so Streamlit never sees them
# being mutated after the widget already exists on the page (which raises
# StreamlitAPIException and breaks the one-click load flow).
if "_load_jd_pending" in st.session_state:
    _pending = st.session_state.pop("_load_jd_pending")
    for _k, _v in _pending.items():
        st.session_state[_k] = _v
elif "_jd_area_pending" in st.session_state:
    # legacy single-key fallback — keep for safety
    st.session_state["jd_area"] = st.session_state.pop("_jd_area_pending")

with col_jd:
    st.subheader("Job Description")
    st.text_area(
        "JD",
        height=280,
        placeholder="Paste JD here, or load from sidebar →",
        label_visibility="collapsed",
        key="jd_area",
    )

# ── JD Metadata (optional, collapsible) ───────────────────────────────────────
with st.expander("📋 JD Metadata (optional — improves Similar JD matching)"):
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        jd_platform = st.selectbox(
            "Platform", ["Not specified", "LinkedIn", "Naukri", "Indeed",
                         "Company Website", "Other"],
            key="jd_platform"
        )
    with m2:
        jd_location = st.text_input("Location", placeholder="e.g. Bengaluru", key="jd_location")
    with m3:
        jd_job_type = st.selectbox(
            "Job Type", ["Not specified", "Full-time", "Part-time", "Contract", "Internship"],
            key="jd_job_type"
        )
    with m4:
        jd_exp_level = st.selectbox(
            "Experience Level", ["Auto-detect", "Entry", "Mid", "Senior"],
            key="jd_exp_level"
        )
    with m5:
        jd_posting_date = st.date_input(
            "Posted Date", value=None, key="jd_posting_date"
        )

st.divider()

# ── Config + run row ──────────────────────────────────────────────────────────
model = "claude-sonnet-4-6"

c1, c2, c3 = st.columns([4, 1, 1])

with c1:
    st.text_input("Company Name", placeholder="e.g. NTT DATA", key="company_input")

with c2:
    st.write(""); st.write("")
    # Key includes a version counter so we can reset the toggle after a run
    # without triggering Streamlit's "can't set widget key externally" error.
    force_fresh = st.toggle(
        "Fresh run",
        value=False,
        key=f"force_fresh_{st.session_state.get('_fresh_key_ver', 0)}",
        help="Bypass cache — re-run all agents even if this JD was analysed before.",
    )

with c3:
    st.write(""); st.write("")
    run_clicked = st.button(
        "🚀  Run Analysis",
        use_container_width=True,
        disabled=st.session_state.running,
        type="primary",
    )

st.caption(
    "🔒 **Privacy:** Your CV is never stored or logged. We save your JD only to power the Similar JDs feature."
)

# ── Validate + run ────────────────────────────────────────────────────────────
if run_clicked:
    cv_text  = st.session_state.get("cv_area", "").strip()
    jd_text  = st.session_state.get("jd_area", "").strip()
    company  = st.session_state.get("company_input", "").strip()

    errors = []
    if not cv_text:  errors.append("CV is empty.")
    if not jd_text:  errors.append("Job Description is empty.")
    if not company:  errors.append("Company name is required.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        errors.append("ANTHROPIC_API_KEY is not set — check your secrets configuration.")

    # ── Concurrent user cap ───────────────────────────────────────────────────
    if not errors:
        try:
            from system_log import get_active_count
            active = get_active_count()
            if active >= 3:
                errors.append(
                    f"The system is currently running {active} analyses. "
                    "Please wait a few minutes and try again."
                )
        except Exception:
            pass

    # ── Session rate limit (1 run per 10 minutes) ─────────────────────────────
    if not errors:
        _last = st.session_state.get("last_run_at", 0)
        _elapsed = time.time() - _last
        _cooldown = 600  # 10 minutes
        if _elapsed < _cooldown:
            _wait = int(_cooldown - _elapsed)
            errors.append(
                f"Please wait {_wait // 60}m {_wait % 60}s before running another analysis. "
                "This protects against accidental double-submissions."
            )

    # ── Hard input length caps ────────────────────────────────────────────────
    if not errors:
        try:
            from sanitizer import validate_input_lengths, InputValidationError
            _len_warnings = validate_input_lengths(cv_text, jd_text)
            for w in _len_warnings:
                st.warning(w)
        except InputValidationError as _e:
            errors.append(str(_e))
        except Exception:
            pass

    # ── Credit check ──────────────────────────────────────────────────────────
    _user_email_for_run = st.session_state.get("user_email", "").strip()
    _credits_active = False
    _beta_free = str(st.secrets.get("BETA_FREE", "false")).lower() in ("true", "1", "yes")
    if not errors and not _beta_free:
        try:
            from credits import credits_configured, get_credits
            if credits_configured() and _user_email_for_run:
                _credits_active = True
                _bal = get_credits(_user_email_for_run)
                if _bal <= 0:
                    errors.append(
                        f"No credits remaining for **{_user_email_for_run}**. "
                        "Please purchase a bundle from the sidebar to continue."
                    )
        except Exception:
            pass  # Credits module unavailable — allow run to proceed

    if errors:
        for e in errors:
            st.error(e)
    else:
        # Pre-extract role info before launching pipeline
        from jd_utils import extract_role_title, extract_experience_level
        jd_text = preprocess_jd(jd_text)  # normalize HTML, PDF artifacts, line breaks
        role_title = extract_role_title(jd_text)

        exp_level_ui = st.session_state.get("jd_exp_level", "Auto-detect")
        if exp_level_ui == "Auto-detect" or not exp_level_ui:
            experience_level = extract_experience_level(jd_text)
        else:
            experience_level = exp_level_ui.lower()

        # Collect JD metadata
        posting_date_val = st.session_state.get("jd_posting_date")
        jd_metadata = {
            "platform":     st.session_state.get("jd_platform", "Not specified"),
            "location":     st.session_state.get("jd_location", ""),
            "job_type":     st.session_state.get("jd_job_type", "Not specified"),
            "posting_date": str(posting_date_val) if posting_date_val else "",
        }

        st.session_state.running    = True
        st.session_state.run_result = None
        st.session_state.last_run_at = time.time()

        # ── Progress UI ───────────────────────────────────────────────────────
        st.subheader("⏳ Running pipeline…")
        st.info(
            f"**Role detected:** {role_title}  ·  **Level:** {experience_level}  \n"
            "**Phase 1** — JD Analysis + Company Research run **in parallel**  \n"
            "**Phase 2** — CV Tailoring starts as soon as Phase 1 JD Analysis is done  \n"
            "Estimated time: 4–8 min. Each section streams in as it completes.  \n"
            "🔒 Your CV is never stored or logged. Your JD is saved only for the Similar JDs feature.",
            icon="ℹ️",
        )
        progress_box = st.empty()

        # ── Live tabs — visible immediately, fill in as each agent finishes ──
        st.divider()
        _PENDING = (
            "*⏳ This section is being generated — "
            "results will appear here once the analysis phase completes.*"
        )
        (ltab1, ltab2, ltab3, ltab4) = st.tabs([
            "📋 JD Analysis", "🏢 Company Intel", "📄 Tailored CV",
            "🔍 Similar JDs",
        ])
        with ltab1: ph_jd = st.empty(); ph_jd.markdown(_PENDING)
        with ltab2: ph_co = st.empty(); ph_co.markdown(_PENDING)
        with ltab3: ph_cv = st.empty(); ph_cv.markdown(_PENDING)
        with ltab4: st.markdown("*Similar JDs will appear after the full analysis completes.*")

        section_placeholders = {
            "jd_analysis":      ph_jd,
            "company_research": ph_co,
            "tailored_cv":      ph_cv,
        }

        start = time.time()
        result = run_pipeline(
            cv_text=cv_text,
            jd_text=jd_text,
            company_name=company,
            model=model,
            role_title=role_title,
            experience_level=experience_level,
            jd_metadata=jd_metadata,
            progress_placeholder=progress_box,
            section_placeholders=section_placeholders,
            force_fresh=force_fresh,
        )
        elapsed = int(time.time() - start)

        st.session_state.run_result = result
        st.session_state.running    = False

        if result and result.get("success"):
            # Increment key version — causes st.toggle to re-render with value=False,
            # preventing accidental expensive re-runs after a successful fresh run.
            st.session_state["_fresh_key_ver"] = (
                st.session_state.get("_fresh_key_ver", 0) + 1
            )

            # ── Deduct 1 credit for the successful run ────────────────────────
            if _beta_free:
                st.info("🎁 Beta access — this run was free.", icon="🎁")
            elif _credits_active and _user_email_for_run:
                try:
                    from credits import deduct_credit, InsufficientCreditsError
                    _new_bal = deduct_credit(_user_email_for_run)
                    st.info(
                        f"1 credit used · **{_new_bal}** remaining for {_user_email_for_run}",
                        icon="💳",
                    )
                except Exception as _dc_exc:
                    st.warning(f"Could not deduct credit: {_dc_exc}", icon="⚠️")

            st.success(f"✅ Done in {elapsed // 60}m {elapsed % 60}s")
        else:
            st.error(f"❌ Failed after {elapsed // 60}m {elapsed % 60}s")

        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
#  RESULTS
# ══════════════════════════════════════════════════════════════════════════════

result = st.session_state.run_result

if result:
    st.divider()

    if not result.get("success"):
        st.error(f"**Error:** {result.get('error', 'Unknown error')}")



    else:
        # Use the company name captured at run-time, not the live input field
        # (user may have edited the field since the run started).
        co = result.get("company_name") or st.session_state.get("company_input", "company")

        # ── Header row ────────────────────────────────────────────────────────
        qr = result.get("quality_report", {})
        st.markdown(
            f'<span style="font-size:1.2em;font-weight:700">✅ Analysis complete — {co}</span>',
            unsafe_allow_html=True,
        )

        # ── ATS keyword match score ───────────────────────────────────────────
        found_n, total_n, found_kws, missing_kws = compute_ats_score(
            result["jd_analysis"], result["tailored_cv"]
        )
        if total_n > 0:
            pct   = int(found_n / total_n * 100)
            color = "#1a7f1a" if pct >= 70 else ("#b87d00" if pct >= 50 else "#b81a1a")
            st.markdown(
                f'<div style="margin:8px 0;padding:8px 14px;background:#f0f4f8;'
                f'border-radius:6px;display:inline-block">'
                f'<span style="font-weight:700;color:{color}">ATS Match: '
                f'{found_n}/{total_n} keywords found ({pct}%)</span>'
                + (f' — missing: <em>{", ".join(missing_kws)}</em>' if missing_kws else "")
                + "</div>",
                unsafe_allow_html=True,
            )

        # ── Download row ──────────────────────────────────────────────────────
        safe_co = (co or "output").replace(" ", "_")
        d1, d2, d3, d4, d5 = st.columns(5)
        with d1:
            st.download_button(
                "📥 Tailored CV (.docx)",
                data=Path(result["cv_path"]).read_bytes(),
                file_name=f"{safe_co}_CV.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        with d2:
            st.download_button(
                "📥 Intel Report (.pdf)",
                data=Path(result["pdf_path"]).read_bytes(),
                file_name=f"{safe_co}_intelligence.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        with d3:
            st.download_button(
                "📥 Raw Outputs (.md)",
                data=Path(result["md_path"]).read_bytes(),
                file_name=f"{safe_co}_raw.md",
                mime="text/markdown",
                use_container_width=True,
            )
        with d4:
            pass
        if False:  # quality report download hidden — internal metric only
            if qr and qr.get("overall_quality") not in (None, "unknown"):
                st.download_button(
                    "📥 Quality Report (.json)",
                    data=json.dumps(qr, indent=2).encode(),
                    file_name=f"{safe_co}_quality.json",
                    mime="application/json",
                    use_container_width=True,
                )
        with d5:
            if st.button("Clear Results", use_container_width=True):
                st.session_state.run_result = None
                st.rerun()

        st.divider()

        # -- Results tabs --------------------------------------------------------
        tab1, tab2, tab3, tab4 = st.tabs([
            "JD Analysis",
            "Company Intel",
            "Tailored CV",
            "Similar JDs",
        ])

        with tab1:
            st.markdown(_normalize_markdown(result["jd_analysis"]))

        with tab2:
            badged = render_source_badges(result["company_research"])
            st.markdown(badged, unsafe_allow_html=True)

        with tab3:
            st.markdown(_normalize_markdown(result["tailored_cv"]))

        with tab4:
            try:
                similar = result.get("similar_jds", [])
                if not similar:
                    st.info(
                        "No similar JDs found yet. Run a few more analyses to "
                        "build up the database and this tab will show similar roles.",
                        icon="ℹ️",
                    )
                else:
                    from jd_database import get_jd_by_id as _get_jd_by_id
                    st.caption(f"Top {len(similar)} most similar JDs in your database:")
                    for jd in similar:
                        sim_pct = int(jd["similarity"] * 100)
                        color = (
                            "#1a7f1a" if sim_pct >= 80
                            else ("#b87d00" if sim_pct >= 60 else "#888888")
                        )
                        parts = []
                        if jd.get("location") and jd["location"] not in ("", "Not specified"):
                            parts.append(jd["location"])
                        if jd.get("platform") and jd["platform"] not in ("", "Not specified"):
                            parts.append(f"via {jd['platform']}")
                        if jd.get("posting_date") and jd["posting_date"] not in ("", "Not specified"):
                            parts.append(f"posted {jd['posting_date']}")
                        if jd.get("job_type") and jd["job_type"] not in ("", "Not specified"):
                            parts.append(jd["job_type"])
                        if jd.get("experience_level"):
                            parts.append(jd["experience_level"].capitalize())
                        expander_label = (
                            f"{sim_pct}% match — **{jd['title']}** at {jd['company']}"
                            + (f"  ·  {' · '.join(parts)}" if parts else "")
                        )
                        with st.expander(expander_label):
                            hcol1, hcol2 = st.columns([5, 1])
                            with hcol1:
                                st.markdown(
                                    f'<span style="font-weight:700;font-size:1.05em">'
                                    f'{jd["title"]}</span> — {jd["company"]}',
                                    unsafe_allow_html=True,
                                )
                                if parts:
                                    st.caption(" · ".join(parts))
                            with hcol2:
                                st.markdown(
                                    f'<div style="text-align:center;padding:4px">'
                                    f'<span style="font-size:1.4em;font-weight:700;'
                                    f'color:{color}">{sim_pct}%</span><br>'
                                    f'<span style="font-size:0.75em;color:#888">match</span>'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )
                            try:
                                full = _get_jd_by_id(jd["id"])
                                if full and full.get("jd_text"):
                                    st.text_area(
                                        "JD",
                                        value=full["jd_text"],
                                        height=250,
                                        disabled=True,
                                        key=f"sim_jd_{jd['id']}",
                                        label_visibility="collapsed",
                                    )
                                    if st.button(
                                        "📋 Load into analysis field",
                                        key=f"load_jd_{jd['id']}",
                                    ):
                                        st.session_state["_load_jd_pending"] = {
                                            "jd_area":       full["jd_text"],
                                            "company_input": full.get("company", ""),
                                            "jd_platform":   "Not specified",
                                            "jd_location":   "",
                                            "jd_job_type":   "Not specified",
                                            "jd_exp_level":  "Auto-detect",
                                            "jd_posting_date": None,
                                        }
                                        st.session_state["running"] = False
                                        st.rerun()
                                else:
                                    st.caption("JD text not available.")
                            except Exception as _je:
                                st.caption(f"Could not load JD: {_je}")
            except Exception as _sim_err:
                st.info(f"Similar JDs unavailable: {_sim_err}", icon="ℹ️")

# ══════════════════════════════════════════════════════════════════════════════
#  FEEDBACK
# ══════════════════════════════════════════════════════════════════════════════

st.divider()
st.markdown("### \U0001f4ac Share Feedback")
st.caption(
    "Help improve the system — takes 30 seconds."
)

with st.form("feedback_form", clear_on_submit=True):
    stars = st.feedback("stars")
    comment = st.text_area(
        "What worked well, or what could be better? (optional)",
        placeholder="e.g. The tailored CV was much stronger. Interview questions felt very relevant.",
        max_chars=1000,
        height=100,
    )
    submitted = st.form_submit_button("Submit Feedback", use_container_width=False)

if submitted:
    if stars is None:
        st.warning("Please select a star rating before submitting.")
    else:
        from feedback_store import save_feedback as _save_fb
        _stars  = stars + 1   # st.feedback returns 0-based index
        _ok, _dest = _save_fb(
            stars   = _stars,
            comment = comment,
            company = st.session_state.get("company_input", ""),
            model   = st.session_state.get("model_choice", ""),
        )
        if _ok:
            st.success(
                f"Thanks for the {'⭐' * _stars} rating!",
                icon="✅",
            )
        else:
            st.error(f"Could not save feedback: {_dest}")

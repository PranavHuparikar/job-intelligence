"""
main.py — Entry point for the Job Intelligence System (CLI).

Architecture:
  Pre-phase:  Role title extraction + experience level extraction
  Phase 1 (parallel):  JD Analysis  ∥  Company Research
  Phase 2 (sequential): CV Tailoring
  Post-phase: Evaluator (LLM-as-judge quality check)

Usage:
    python main.py

Outputs saved to ./outputs/<company>_<timestamp>/
  - tailored_cv.docx
  - job_intelligence.pdf
  - raw_outputs.md
  - quality_report.json

SECURITY NOTE: CV bytes are never written to disk. CV text exists only in memory
during the pipeline run. See cv_utils.py for the in-memory contract.
"""

import os
import re
import sys
import threading
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from crewai import Crew, Process

from agents import build_agents
from tasks import build_tasks
from cv_utils import read_cv, word_count
from sanitizer import sanitize_cv_and_jd
from jd_utils import extract_role_title, extract_experience_level, preprocess_jd
from output_generator import generate_cv_docx, generate_report_pdf
from system_log import start_run, log_event, end_run as _log_end_run


# ── Load environment ──────────────────────────────────────────────────────────
load_dotenv()

# Optional spend guard — set DAILY_COST_CAP_USD in .env to enable
_DAILY_CAP_USD = float(os.getenv("DAILY_COST_CAP_USD", "0"))  # 0 = disabled


# ── Helpers ───────────────────────────────────────────────────────────────────

def read_jd() -> str:
    print("\nPaste the Job Description. Type DONE on a new line when finished:\n")
    lines = []
    while True:
        line = input()
        if line.strip().upper() == "DONE":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def make_output_dir(company: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in company).strip("_")
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    out  = Path("outputs") / f"{safe}_{ts}"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ── Spend guard ───────────────────────────────────────────────────────────────

def _check_spend_guard():
    """
    Abort with a clean message if a daily cost log exists and exceeds the cap.
    Only active if DAILY_COST_CAP_USD is set in .env.
    """
    if _DAILY_CAP_USD <= 0:
        return  # guard disabled

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = Path(".cache") / f"cost_log_{today}.json"

    if log_file.exists():
        import json
        try:
            data = json.loads(log_file.read_text())
            spent = float(data.get("total_usd", 0))
            if spent >= _DAILY_CAP_USD:
                print(
                    f"\n[ABORTED] Daily cost cap of ${_DAILY_CAP_USD:.2f} reached "
                    f"(spent today: ${spent:.2f}). Try again tomorrow.\n"
                )
                sys.exit(1)
        except Exception:
            pass


# ── Dependency-graph parallel execution ──────────────────────────────────────
#
# Dependency graph:
#   T0 (JD Analysis)       — no deps
#   T1 (Company Research)  — no deps
#   T2 (CV Tailor)         — needs T0 only  ← starts as soon as T0 done
#
# Key optimisation: T2 no longer waits for T1 to finish.
# T1 tail (web search) overlaps with T2 (CV writing), saving ~30-60 s/run.

def run_pipeline_parallel(agents: dict, tasks: list) -> tuple:
    """
    Execute all 3 tasks with maximum concurrency, respecting dependencies.
    Returns (jd_analysis, company_research, tailored_cv).
    """
    errors: dict = {}
    t0_done = threading.Event()
    t1_done = threading.Event()
    _saved_outputs: dict = {}

    _RETRY_DELAYS = [5, 15, 45]

    def _run_task(agent_key: str, idx: int,
                  wait_for: list = None, signals: list = None,
                  pre_hook=None) -> None:
        try:
            if wait_for:
                for ev in wait_for:
                    ev.wait()
            if errors:
                return
            if pre_hook:
                pre_hook()

            for attempt in range(len(_RETRY_DELAYS) + 1):
                try:
                    Crew(
                        agents=[agents[agent_key]],
                        tasks=[tasks[idx]],
                        process=Process.sequential,
                        verbose=True,
                    ).kickoff()
                    break
                except Exception as exc:
                    err_str = str(exc).lower()
                    is_rate_limit = any(
                        x in err_str for x in
                        ["rate limit", "429", "ratelimit", "too many requests",
                         "overloaded", "quota"]
                    )
                    if is_rate_limit and attempt < len(_RETRY_DELAYS):
                        import time as _t
                        wait_secs = _RETRY_DELAYS[attempt]
                        print(f"  ⟳ Rate limit on {agent_key} — "
                              f"retrying in {wait_secs}s (attempt {attempt + 2}/4)…")
                        _t.sleep(wait_secs)
                    else:
                        raise

        except Exception as e:
            errors[agent_key] = e
        finally:
            if tasks[idx].output and tasks[idx].output.raw:
                _saved_outputs[idx] = tasks[idx].output.raw
            if signals:
                for ev in signals:
                    ev.set()

    # ── Company research cache lookup (main.py / CLI path) ───────────────────
    _company_cache = None
    try:
        from company_cache import get_cached_research as _get_company_cache
        _company_cache = _get_company_cache(company_name)
        if _company_cache:
            from crewai.tasks.task_output import TaskOutput
            tasks[1].output = TaskOutput(
                description="(cached)", raw=_company_cache, agent="company_researcher"
            )
            t1_done.set()
            print("  ⚡ Company research cached — skipping T1")
    except Exception:
        _company_cache = None

    _threads = [
        threading.Thread(
            target=_run_task,
            kwargs={"agent_key": "jd_analyst",        "idx": 0,
                    "wait_for": [],        "signals": [t0_done]},
            name="JD-Analyst",
        ),
    ]
    if not _company_cache:
        _threads.append(threading.Thread(
            target=_run_task,
            kwargs={"agent_key": "company_researcher", "idx": 1,
                    "wait_for": [],        "signals": [t1_done]},
            name="Company-Researcher",
        ))
    _threads += [
        threading.Thread(
            target=_run_task,
            kwargs={"agent_key": "cv_tailor",         "idx": 2,
                    "wait_for": [t0_done], "signals": []},
            name="CV-Tailor",
        ),
    ]

    print("\n  ┌─ Pipeline: all 3 agents starting (dependency-graph parallel) ─┐")
    for t in _threads:
        t.start()

    t0_done.wait(); print("  ✓ JD Analysis done  →  CV Tailoring starting…")
    t1_done.wait(); print("  ✓ Company Research done")
    for t in _threads:
        t.join()

    # Cache company research for future runs (24h TTL, best-effort)
    if not _company_cache and tasks[1].output and tasks[1].output.raw:
        try:
            from company_cache import cache_research as _cache_company
            _cache_company(company_name, _saved_outputs.get(1) or tasks[1].output.raw)
        except Exception:
            pass
    print("  └─ All tasks done.\n")

    if errors:
        agent_key, exc = next(iter(errors.items()))
        print(f"\n[ABORTED] {agent_key} failed: {exc}")
        sys.exit(1)

    for idx, label in [(0, "JD Analysis"), (1, "Company Research"),
                       (2, "CV Tailoring")]:
        if tasks[idx].output is None or not getattr(tasks[idx].output, "raw", None):
            print(
                f"\n[ABORTED] {label} produced no output. "
                "This usually means the agent hit its max_iter limit or the LLM "
                "returned an empty response. Try re-running with --model opus, or "
                "check for token-limit warnings in the verbose output above."
            )
            sys.exit(1)

    return (tasks[0].output.raw, tasks[1].output.raw,
            tasks[2].output.raw)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  JOB INTELLIGENCE SYSTEM")
    print("  Powered by Claude + CrewAI")
    print("=" * 60)

    # ── API key check ────────────────────────────────────────────────────────
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n[ERROR] ANTHROPIC_API_KEY not set. Add it to .env.\n")
        sys.exit(1)

    # ── Spend guard ───────────────────────────────────────────────────────────
    _check_spend_guard()

    # ── CV ────────────────────────────────────────────────────────────────────
    cv_path = input("\nPath to your CV (.pdf / .docx / .txt): ").strip()
    try:
        cv_text = read_cv(cv_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)

    wc = word_count(cv_text)
    if wc > 1200:
        print(f"  [WARNING] CV is {wc} words — consider trimming to <1200.")
    else:
        print(f"  CV loaded: {wc} words")

    # ── JD ────────────────────────────────────────────────────────────────────
    jd_text = read_jd()
    if not jd_text:
        print("\n[ERROR] No JD provided.")
        sys.exit(1)

    # ── Sanitize inputs ───────────────────────────────────────────────────────
    cv_text, jd_text, warnings = sanitize_cv_and_jd(cv_text, jd_text)
    if warnings:
        print("\n  [SECURITY] Suspicious patterns detected and neutralised:")
        for w in warnings:
            print(f"    ⚠ {w}")

    # ── Company name ──────────────────────────────────────────────────────────
    company = input("\nCompany name: ").strip()
    if not company:
        print("\n[ERROR] Company name required.")
        sys.exit(1)

    # ── Pre-extract role info before parallel phase ───────────────────────────
    jd_text          = preprocess_jd(jd_text)   # normalize HTML, PDF artifacts, line breaks
    role_title       = extract_role_title(jd_text)
    experience_level = extract_experience_level(jd_text)
    print(f"\n  Detected role:       {role_title}")
    print(f"  Experience level:    {experience_level}")

    # ── Model selection ───────────────────────────────────────────────────────
    print("\nModel:")
    print("  1. claude-sonnet-4-6          ← Recommended")
    print("  2. claude-haiku-4-5-20251001  ← Faster / cheaper")
    print("  3. claude-opus-4-6            ← Best quality")
    choice = input("Choose [1/2/3] (default 1): ").strip() or "1"
    model = {
        "1": "claude-sonnet-4-6",
        "2": "claude-haiku-4-5-20251001",
        "3": "claude-opus-4-6",
    }.get(choice, "claude-sonnet-4-6")
    print(f"  Using: {model}\n")

    print("=" * 60)
    print(f"  Analysing: {company}  |  {role_title}  |  {experience_level}")
    print("=" * 60)

    # ── System log: start run ────────────────────────────────────────────────
    import time as _time
    _run_start  = _time.time()
    _run_id     = start_run(company=company, model=model,
                            experience_level=experience_level, source="cli")

    # ── Build agents and tasks ────────────────────────────────────────────────
    agents = build_agents(model)
    tasks  = build_tasks(
        agents, jd_text, cv_text, company,
        role_title=role_title,
        experience_level=experience_level,
        job_location="",   # CLI: no location collected — defaults to India sources
    )

    # ── Run all 3 agents (dependency-graph parallel) ──────────────────────────
    log_event(_run_id, "phase1", "start")
    jd_analysis, company_research, tailored_cv = \
        run_pipeline_parallel(agents, tasks)
    log_event(_run_id, "phase1", "done")
    log_event(_run_id, "phase2", "done")

    # ── Outputs ───────────────────────────────────────────────────────────────
    out = make_output_dir(company)
    print(f"  Generating outputs → {out}\n")

    cv_path_out  = str(out / "tailored_cv.docx")
    pdf_path_out = str(out / "job_intelligence.pdf")

    print("  Creating CV (.docx)…")
    generate_cv_docx(tailored_cv, cv_path_out)

    print("  Creating report (.pdf)…")
    generate_report_pdf(
        company_name=company,
        jd_analysis=jd_analysis,
        company_research=company_research,
        output_path=pdf_path_out,
    )

    (out / "raw_outputs.md").write_text(
        f"# JD Analysis\n\n{jd_analysis}\n\n"
        f"# Company Intelligence\n\n{company_research}\n\n"
        f"# Tailored CV\n\n{tailored_cv}\n",
        encoding="utf-8",
    )

    # ── Quality evaluation ────────────────────────────────────────────────────
    print("  Running quality evaluation (Haiku)…")
    quality = "unknown"
    score   = -1
    try:
        from evaluator import evaluate_pipeline_output
        report  = evaluate_pipeline_output(
            cv_text=cv_text,
            jd_text=jd_text,
            jd_analysis=jd_analysis,
            tailored_cv=tailored_cv,
            output_dir=str(out),
        )
        quality = report.get("overall_quality", "unknown")
        score   = report.get("overall_score", -1)
        print(f"  Quality: {quality.upper()} ({score}/100)")
        if report.get("fabrication_flags"):
            print(f"  Fabrication flags: {report['fabrication_flags']}")
    except Exception as e:
        print(f"  [WARNING] Evaluator failed: {e}")

    # -- System log: end run --------------------------------------------------
    from jd_utils import compute_ats_score as _ats
    try:
        fn, tn, _, _ = _ats(jd_analysis, tailored_cv)
        ats_str = f"{fn}/{tn}" if tn > 0 else None
    except Exception:
        ats_str = None
    _log_end_run(
        run_id=_run_id,
        success=True,
        duration_secs=_time.time() - _run_start,
        model=model,
        quality=quality,
        ats_score=ats_str,
    )

    print("\n" + "=" * 60)
    print("  DONE")
    print("=" * 60)
    print(f"\n  tailored_cv.docx     -> {cv_path_out}")
    print(f"  job_intelligence.pdf -> {pdf_path_out}")
    print(f"  raw_outputs.md       -> {out / 'raw_outputs.md'}")
    print(f"  quality_report.json  -> {out / 'quality_report.json'}\n")


if __name__ == "__main__":
    main()

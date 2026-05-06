"""
system_log.py -- Structured system logger for the Job Intelligence System.

What it tracks:
  - Every pipeline run: start, phase events, completion, duration, cost estimate
  - Active run registry: thread-safe, gives live concurrent-user count
  - Daily JSONL log: one JSON object per line, rotated by date
  - Daily cost accumulator: feeds the spend guard in main.py

Security constraints (enforced here):
  - CV text, JD text, and personal data are NEVER written to any log
  - Only: run_id, company name, model, phase events, timing, outcome

Log location: ./logs/  (one file per day: run_log_YYYY-MM-DD.jsonl)
Cost log:     ./.cache/cost_log_YYYY-MM-DD.json

Usage:
    from system_log import start_run, end_run, log_event, get_active_count

    run_id = start_run(company="Acme", model="claude-sonnet-4-6", experience_level="senior")
    log_event(run_id, "phase1_jd", "done", detail="fit_score=75")
    end_run(run_id, success=True, duration_secs=312, model="claude-sonnet-4-6")
    print(get_active_count())   # -> int
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

# Directories
_LOG_DIR   = Path(os.getenv("LOG_DIR",   "logs"))
_CACHE_DIR = Path(os.getenv("CACHE_DIR", ".cache"))

# Approximate cost per run by model (USD) -- used for daily cost accumulator
# Based on ~25K input tokens + ~8K output tokens per full pipeline run
_COST_ESTIMATE: dict = {
    "claude-sonnet-4-6":         0.30,   # $3/M in, $15/M out
    "claude-haiku-4-5-20251001": 0.05,   # $0.80/M in, $4/M out
    "claude-opus-4-6":           1.20,   # $15/M in, $75/M out
}
_DEFAULT_COST = 0.30

# Thread-safe active run registry
# Structure: { run_id: { "company": str, "model": str, "started_at": float, ... } }
_active_runs: dict = {}
_registry_lock = threading.Lock()

# Separate write locks — run log and cost log are independent files
_run_log_lock  = threading.Lock()
_cost_log_lock = threading.Lock()

# Maximum age of an active run before it is considered stale (crashed without cleanup)
_ACTIVE_RUN_TTL_SECS = 1800  # 30 minutes


# -- Public API ----------------------------------------------------------------

def start_run(
    company:          str,
    model:            str,
    experience_level: str = "",
    source:           str = "streamlit",  # "streamlit" | "cli"
) -> str:
    """
    Register a new pipeline run. Returns a run_id to pass to subsequent calls.
    Call this before any LLM call.
    """
    run_id = str(uuid.uuid4())[:12]
    started_at = time.time()

    with _registry_lock:
        _active_runs[run_id] = {
            "run_id":          run_id,
            "company":         company,          # company name is OK to log
            "model":           model,
            "experience_level": experience_level,
            "source":          source,
            "started_at":      started_at,
            "phases":          [],
        }

    _write_event({
        "event":          "run_start",
        "run_id":         run_id,
        "company":        company,
        "model":          model,
        "experience_level": experience_level,
        "source":         source,
        "active_count":   len(_active_runs),
    })
    return run_id


def log_event(
    run_id:        str,
    phase:         str,
    event:         str,
    detail:        Optional[str] = None,
) -> None:
    """
    Log a pipeline phase event.
    phase: "phase1_jd" | "phase1_company" | "phase2_cv" | "phase2_interview" | "evaluator"
    event: "start" | "done" | "error"
    detail: short non-PII string, e.g. "fit_score=75" or "error=RateLimitError"
    """
    with _registry_lock:
        if run_id in _active_runs:
            _active_runs[run_id]["phases"].append({
                "phase": phase, "event": event, "t": time.time()
            })

    payload: dict = {
        "event":  f"{phase}_{event}",
        "run_id": run_id,
        "phase":  phase,
    }
    if detail:
        payload["detail"] = detail[:200]   # cap to prevent accidental PII

    _write_event(payload)


def end_run(
    run_id:        str,
    success:       bool,
    duration_secs: float,
    model:         str       = "",
    error_type:    Optional[str] = None,   # exception class name only, no message
    quality:       Optional[str] = None,   # "green" | "amber" | "red" | "unknown"
    ats_score:     Optional[str] = None,   # e.g. "8/10"
) -> None:
    """
    Mark a run as complete. Removes it from active registry and logs outcome.
    Also updates the daily cost accumulator.
    """
    with _registry_lock:
        run_meta = _active_runs.pop(run_id, {})

    used_model = model or run_meta.get("model", "")
    cost_usd   = _COST_ESTIMATE.get(used_model, _DEFAULT_COST)

    payload: dict = {
        "event":        "run_end",
        "run_id":       run_id,
        "company":      run_meta.get("company", ""),
        "model":        used_model,
        "success":      success,
        "duration_secs": round(duration_secs, 1),
        "cost_usd_est": cost_usd,
        "active_count": len(_active_runs),
    }
    if quality:
        payload["quality"] = quality
    if ats_score:
        payload["ats_score"] = ats_score
    if not success and error_type:
        payload["error_type"] = error_type    # class name only, e.g. "RateLimitError"

    _write_event(payload)

    if success:
        _accumulate_cost(cost_usd)


def get_active_count() -> int:
    """Return the number of currently running pipelines (proxy for concurrent users).
    Evicts stale entries from runs that crashed without calling end_run()."""
    now = time.time()
    with _registry_lock:
        stale = [
            rid for rid, m in _active_runs.items()
            if now - m.get("started_at", now) > _ACTIVE_RUN_TTL_SECS
        ]
        for rid in stale:
            del _active_runs[rid]
        return len(_active_runs)


def get_active_runs() -> list:
    """
    Return a sanitised snapshot of active runs for the admin view.
    Returns list of dicts with: run_id (truncated), company, model,
    experience_level, elapsed_secs.
    No personal data.
    """
    now = time.time()
    with _registry_lock:
        snapshot = []
        for run_id, meta in _active_runs.items():
            snapshot.append({
                "run_id":          run_id[:8] + "...",
                "company":         meta.get("company", ""),
                "model":           meta.get("model", ""),
                "experience_level": meta.get("experience_level", ""),
                "elapsed_secs":    round(now - meta.get("started_at", now)),
            })
    return snapshot


def get_daily_cost() -> float:
    """Return today's estimated cumulative cost in USD."""
    today    = datetime.now().strftime("%Y-%m-%d")
    log_file = _CACHE_DIR / f"cost_log_{today}.json"
    if not log_file.exists():
        return 0.0
    try:
        data = json.loads(log_file.read_text(encoding="utf-8"))
        return float(data.get("total_usd", 0.0))
    except Exception:
        return 0.0


def get_today_stats() -> dict:
    """
    Parse today's JSONL log and return summary stats:
      runs_total, runs_succeeded, runs_failed, avg_duration_secs,
      daily_cost_usd, quality_counts.
    """
    today    = datetime.now().strftime("%Y-%m-%d")
    log_file = _LOG_DIR / f"run_log_{today}.jsonl"

    stats = {
        "runs_total":        0,
        "runs_succeeded":    0,
        "runs_failed":       0,
        "avg_duration_secs": 0.0,
        "daily_cost_usd":    get_daily_cost(),
        "quality_counts":    {"green": 0, "amber": 0, "red": 0, "unknown": 0},
    }

    if not log_file.exists():
        return stats

    durations = []
    try:
        for line in log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") != "run_end":
                continue
            stats["runs_total"] += 1
            if obj.get("success"):
                stats["runs_succeeded"] += 1
            else:
                stats["runs_failed"] += 1
            if obj.get("duration_secs"):
                durations.append(obj["duration_secs"])
            q = obj.get("quality", "unknown")
            if q in stats["quality_counts"]:
                stats["quality_counts"][q] += 1
    except Exception:
        pass

    if durations:
        stats["avg_duration_secs"] = round(sum(durations) / len(durations), 1)

    return stats


# -- Internal helpers ----------------------------------------------------------

def _write_event(payload: dict) -> None:
    """Append a JSON event line to today's log file. Thread-safe."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    today    = datetime.now().strftime("%Y-%m-%d")
    log_file = _LOG_DIR / f"run_log_{today}.jsonl"

    payload["ts"] = datetime.now().isoformat(timespec="seconds")

    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _run_log_lock:
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass   # log failure must never crash the pipeline


def _accumulate_cost(cost_usd: float) -> None:
    """Add cost_usd to today's cost accumulator file."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today    = datetime.now().strftime("%Y-%m-%d")
    log_file = _CACHE_DIR / f"cost_log_{today}.json"

    with _cost_log_lock:
        try:
            current = 0.0
            if log_file.exists():
                data = json.loads(log_file.read_text(encoding="utf-8"))
                current = float(data.get("total_usd", 0.0))
            new_total = round(current + cost_usd, 4)
            log_file.write_text(
                json.dumps({"date": today, "total_usd": new_total, "updated_at": datetime.now().isoformat()}),
                encoding="utf-8",
            )
        except Exception:
            pass

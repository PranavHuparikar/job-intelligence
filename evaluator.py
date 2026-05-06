"""
evaluator.py — LLM-as-judge quality evaluation for the Job Intelligence System.

Uses Claude Haiku (cost-efficient) to evaluate every pipeline run on 4 dimensions:
  1. Fabrication check  — did the tailored CV or STAR stories invent facts?
  2. Keyword coverage   — how many top JD keywords appear in the tailored CV?
  3. Math accuracy      — is the fit score arithmetic correct?
  4. STAR grounding     — are STAR stories anchored to actual CV bullets?

Returns a quality_report dict and writes quality_report.json to the output dir.
The overall_quality field drives the badge in the Streamlit UI:
  green  → score ≥ 88
  amber  → score 68–87
  red    → score < 68
  unknown → evaluation failed

Usage:
  from evaluator import evaluate_pipeline_output
  report = evaluate_pipeline_output(cv_text, jd_text, jd_analysis, tailored_cv, interview_prep)
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional


_DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Weights for overall score
_WEIGHTS = {
    "fabrication_score": 0.40,  # most critical
    "keyword_coverage":  0.25,
    "math_accuracy":     0.20,
    "star_grounding":    0.15,
}


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate_pipeline_output(
    cv_text:        str,
    jd_text:        str,
    jd_analysis:    str,
    tailored_cv:    str,
    model:          str = _DEFAULT_MODEL,
    output_dir:     Optional[str] = None,
) -> dict:
    """
    Run LLM-as-judge evaluation. Non-blocking on failure — returns a fallback
    report with overall_quality='unknown' if the API call fails.
    """
    try:
        import litellm
    except ImportError:
        return _fallback_report("litellm not available")

    star_section = ""  # interview prep removed

    prompt = f"""You are a strict quality auditor for a job application AI assistant.
Evaluate the pipeline outputs below for factual integrity and quality.

=== ORIGINAL CV (ground truth) ===
{cv_text[:8000]}

=== JOB DESCRIPTION ===
{jd_text[:1500]}

=== JD ANALYSIS OUTPUT ===
{jd_analysis[:2000]}

=== TAILORED CV OUTPUT ===
{tailored_cv[:2000]}

=== STAR STORIES FROM INTERVIEW PREP ===
{star_section}

Evaluate each dimension carefully, then respond with ONLY valid JSON (no markdown, no preamble):

{{
  "fabrication_score": <0-100>,
  "fabrication_flags": ["exact quote of any invented fact, name, number, or claim not verifiable in original CV"],
  "keyword_coverage": <0-100>,
  "keywords_missing": ["top JD keywords not found verbatim or near-verbatim in tailored CV"],
  "math_accuracy": <0-100>,
  "math_notes": "Show the fit score arithmetic. State whether the final score is correct.",
  "star_grounding": <0-100>,
  "star_flags": ["any STAR story claim not traceable to a specific line in the original CV"],
  "overall_quality": "<green|amber|red>",
  "overall_score": <0-100>,
  "summary": "2-3 sentence quality assessment for the operator."
}}

Scoring rubric:
- fabrication_score: 100 = zero fabrication. Deduct 15 pts per invented proper noun (company/project/name), 10 pts per invented number, 5 pts per unsupported claim.
- keyword_coverage: Count top 10 JD keywords (from ## TOP JD KEYWORDS in the analysis). Score = (found/total)*100.
- math_accuracy: Verify mandatory_met/total*50 + preferred_met/total*30 + exp_pts. Score 100 if correct ±5, 50 if off by 6-15, 0 if wrong by more than 15.
- star_grounding: 100 = every STAR story cites an exact CV bullet AND all 3 stories are complete.
  Deduct 25 pts per story with no traceable CV source.
  Deduct 30 pts if any story is visibly truncated or incomplete (missing Result section or cut off mid-sentence).
  Add "INCOMPLETE: Story N cut off" to star_flags for any truncated story.
- overall_quality: green if overall_score ≥ 88, amber if 68-87, red if < 68.
- overall_score: fabrication*0.40 + keyword*0.25 + math*0.20 + star*0.15 (rounded to integer)."""

    try:
        response = litellm.completion(
            model=f"anthropic/{model}",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        report = _parse_json_response(raw)

        # Recompute overall_score locally as sanity check
        scores = {
            "fabrication_score": report.get("fabrication_score", 0),
            "keyword_coverage":  report.get("keyword_coverage", 0),
            "math_accuracy":     report.get("math_accuracy", 0),
            "star_grounding":    report.get("star_grounding", 0),
        }
        computed = int(sum(scores[k] * w for k, w in _WEIGHTS.items()))
        report["overall_score"] = computed

        # Re-derive badge from computed score
        if computed >= 88:
            report["overall_quality"] = "green"
        elif computed >= 68:
            report["overall_quality"] = "amber"
        else:
            report["overall_quality"] = "red"

        report["evaluated_at"] = datetime.now().isoformat()
        report["model_used"]   = model

        if output_dir:
            out = Path(output_dir) / "quality_report.json"
            out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        return report

    except Exception as e:
        return _fallback_report(f"{type(e).__name__}: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_star_section(interview_prep: str) -> str:
    """Extract just the STAR stories section (up to 2000 chars)."""
    lines = interview_prep.split("\n")
    in_star = False
    star_lines = []
    for line in lines:
        if re.search(r"STAR\s+STOR", line, re.IGNORECASE):
            in_star = True
        elif in_star and line.startswith("## ") and not re.search(r"STAR", line, re.IGNORECASE):
            break
        if in_star:
            star_lines.append(line)
    return "\n".join(star_lines)[:5000] if star_lines else interview_prep[:2500]


def _parse_json_response(raw: str) -> dict:
    """Extract JSON from the model response, tolerating markdown fences."""
    original = raw  # keep for error logging
    # Strip markdown fences
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()

    # Find outermost {...}
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start >= 0 and end > start:
        raw = raw[start:end]

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # Log the raw response so operators can debug prompt/output mismatches
        print(
            f"[evaluator] JSON parse failed: {exc}\n"
            f"  Raw response (first 500 chars): {original[:500]!r}"
        )
        raise


def _fallback_report(error: str) -> dict:
    return {
        "fabrication_score": -1,
        "fabrication_flags": [],
        "keyword_coverage":  -1,
        "keywords_missing":  [],
        "math_accuracy":     -1,
        "math_notes":        "Evaluation failed.",
        "star_grounding":    -1,
        "star_flags":        [],
        "overall_quality":   "unknown",
        "overall_score":     -1,
        "summary":           f"Quality evaluation could not complete: {error}",
        "evaluated_at":      datetime.now().isoformat(),
        "error":             error,
    }


# ── Quality badge helper (for Streamlit) ─────────────────────────────────────

def quality_badge_html(report: dict) -> str:
    """Return an HTML badge string for the Streamlit results header."""
    quality = report.get("overall_quality", "unknown")
    score   = report.get("overall_score", -1)

    colours = {
        "green":   ("#1a7f1a", "✓ Quality: Good"),
        "amber":   ("#b87d00", "⚠ Quality: Review"),
        "red":     ("#b81a1a", "✗ Quality: Issues Found"),
        "unknown": ("#555555", "○ Quality: Not Evaluated"),
    }
    bg, label = colours.get(quality, colours["unknown"])
    score_str = f" ({score}/100)" if score >= 0 else ""

    return (
        f'<span style="background:{bg};color:white;padding:3px 10px;'
        f'border-radius:4px;font-size:0.85em;font-weight:600">'
        f'{label}{score_str}</span>'
    )

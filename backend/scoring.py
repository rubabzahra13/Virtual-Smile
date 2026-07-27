"""
scoring.py

This is the business logic layer. It does NOT call any AI model. It takes
whatever raw text a model returned, tries to parse it as the expected JSON
shape, and computes the final overall score using fixed, deterministic
weights that live in code — not inside a prompt.

Keeping this separate from the model call is the whole reason for the
two-layer architecture: the scoring rule is the same no matter which
provider produced the underlying findings, so scores are comparable
across providers.
"""

import json
import re

# Fixed category weights. Change these here, not in the prompt, if you
# want to re-weight what matters most for the overall score.
CATEGORY_WEIGHTS = {
    "alignment": 0.20,
    "gum_health": 0.25,
    "color": 0.15,
    "restorations": 0.20,
    "missing_teeth": 0.20,
}

REQUIRED_CATEGORIES = list(CATEGORY_WEIGHTS.keys())


class ScoringError(Exception):
    """Raised when a model's output could not be parsed into a usable score."""


def _extract_json_block(raw_text: str) -> str:
    """
    Models sometimes wrap JSON in ```json ... ``` fences even when told
    not to. Strip that before parsing rather than failing outright.
    """
    if raw_text is None:
        raise ScoringError("Model returned no text content.")

    text = raw_text.strip()

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)

    # Fallback: grab the first {...} block in the text.
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        return brace_match.group(0)

    raise ScoringError("No JSON object found in model output.")


def parse_and_score(raw_text: str) -> dict:
    """
    Returns:
    {
        "parsed_ok": bool,
        "findings": <original parsed dict, or None>,
        "overall_score": int or None,
        "category_scores": dict or None,
        "parse_error": str or None,
    }
    """
    try:
        json_str = _extract_json_block(raw_text)
        findings = json.loads(json_str)
    except (ScoringError, json.JSONDecodeError) as e:
        return {
            "parsed_ok": False,
            "findings": None,
            "overall_score": None,
            "category_scores": None,
            "parse_error": str(e),
        }

    scores = findings.get("scores", {})
    missing = [c for c in REQUIRED_CATEGORIES if c not in scores]
    if missing:
        return {
            "parsed_ok": False,
            "findings": findings,
            "overall_score": None,
            "category_scores": scores,
            "parse_error": f"Missing expected score categories: {missing}",
        }

    weighted_sum = sum(scores[cat] * weight for cat, weight in CATEGORY_WEIGHTS.items())
    overall_score = round(weighted_sum)

    return {
        "parsed_ok": True,
        "findings": findings,
        "overall_score": overall_score,
        "category_scores": scores,
        "parse_error": None,
    }

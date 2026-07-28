"""
scoring.py

This is the business logic layer. It does NOT call any AI model. It takes
whatever raw text a model returned, tries to parse it as the expected JSON
shape, and computes the final overall score using fixed, deterministic
weights that live in code - not inside a prompt.

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

MIN_SIGN_CONFIDENCE = 0.6
ALLOWED_EVIDENCE_STRENGTH = {"high", "medium"}
GUM_SIGN_TYPES = {"calculus", "gum_recession", "gingivitis", "gum_inflammation", "plaque"}
ALIGNMENT_SIGN_TYPES = {"crowding", "spacing", "rotation", "arch_irregularity", "malalignment"}


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
    overall_score = _compute_overall_score(scores)

    return {
        "parsed_ok": True,
        "findings": findings,
        "overall_score": overall_score,
        "category_scores": scores,
        "parse_error": None,
    }


def _compute_overall_score(scores: dict) -> int:
    weighted_sum = sum(scores[cat] * weight for cat, weight in CATEGORY_WEIGHTS.items())
    return round(weighted_sum)


def _concerns_from_observed_signs(findings: dict) -> list:
    signs = findings.get("observed_signs", [])
    labels = []
    seen = set()
    for sign in signs:
        if not isinstance(sign, dict):
            continue
        strength = str(sign.get("evidence_strength", "")).lower()
        confidence = sign.get("confidence")
        if strength not in ALLOWED_EVIDENCE_STRENGTH:
            continue
        if confidence is not None and float(confidence) < MIN_SIGN_CONFIDENCE:
            continue
        label = sign.get("concern_label")
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def _is_gum_related(sign: dict) -> bool:
    sign_name = str(sign.get("sign", "")).lower()
    concern_label = str(sign.get("concern_label", "")).lower()
    if sign_name in GUM_SIGN_TYPES:
        return True
    if any(token in concern_label for token in ("gum", "gingiv", "plaque", "calculus", "tartar")):
        return True
    return False


def _is_alignment_related(sign: dict) -> bool:
    sign_name = str(sign.get("sign", "")).lower()
    concern_label = str(sign.get("concern_label", "")).lower()
    if sign_name in ALIGNMENT_SIGN_TYPES:
        return True
    if any(token in concern_label for token in ("crowd", "space", "align", "rotation", "overlap")):
        return True
    return False


def sanitize_detection_findings(findings: dict) -> dict:
    """
    Enforce conservative safety/consistency rules:
 - No gum findings if gums are marked not visible.
 - Alignment score should map to an alignment concern when clearly reduced.
    """
    if not isinstance(findings, dict):
        return findings

    normalized = dict(findings)
    visibility = normalized.get("visibility", {}) or {}
    signs = normalized.get("observed_signs", [])
    if not isinstance(signs, list):
        signs = []

    filtered_signs = signs
    gums_visible = visibility.get("gums_visible")
    not_assessable = normalized.get("not_assessable_from_photo", [])
    if not isinstance(not_assessable, list):
        not_assessable = []

    if gums_visible is False:
        filtered_signs = [s for s in signs if not isinstance(s, dict) or not _is_gum_related(s)]
        if "gum_health" not in not_assessable:
            not_assessable.append("gum_health")
        scores = normalized.get("scores", {})
        if isinstance(scores, dict) and "gum_health" in scores:
            scores["gum_health"] = 100
        normalized["scores"] = scores

    normalized["observed_signs"] = filtered_signs
    normalized["not_assessable_from_photo"] = not_assessable

    return normalized


def parse_detection(raw_text: str) -> dict:
    """
  Parse Pass 1 detection JSON (scores + observed_signs, no patient report fields).
    """
    try:
        json_str = _extract_json_block(raw_text)
        findings = sanitize_detection_findings(json.loads(json_str))
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

    return {
        "parsed_ok": True,
        "findings": findings,
        "overall_score": _compute_overall_score(scores),
        "category_scores": scores,
        "parse_error": None,
        "derived_concerns": _concerns_from_observed_signs(findings),
    }


def parse_explanation(raw_text: str) -> dict:
    """Parse Pass 2 patient-facing JSON (no scores required)."""
    try:
        json_str = _extract_json_block(raw_text)
        findings = json.loads(json_str)
    except (ScoringError, json.JSONDecodeError) as e:
        return {
            "parsed_ok": False,
            "findings": None,
            "parse_error": str(e),
        }

    return {
        "parsed_ok": True,
        "findings": findings,
        "parse_error": None,
    }


def merge_findings(detection: dict, explanation: dict) -> dict:
    """Combine Pass 1 detection data with Pass 2 patient-facing fields."""
    merged = dict(detection)
    for key in (
        "visible_concerns",
        "concern_details",
        "priority_level",
        "priority_order",
        "treatment_roadmap",
        "notes",
    ):
        if key in explanation:
            merged[key] = explanation[key]

    derived = _concerns_from_observed_signs(merged)
    if derived:
        merged["visible_concerns"] = derived

    confidence = detection.get("confidence_breakdown", {}).get("overall_assessment")
    if confidence is not None:
        merged["confidence"] = confidence

    return merged


def score_from_detection(findings: dict) -> dict:
    """Score from merged or raw detection findings (scores live in detection pass)."""
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

    return {
        "parsed_ok": True,
        "findings": findings,
        "overall_score": _compute_overall_score(scores),
        "category_scores": scores,
        "parse_error": None,
    }

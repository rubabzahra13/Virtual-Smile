"""
Orchestrates single-pass and two-pass analysis pipelines.
"""

import json
from typing import List, Optional, Tuple

from image_quality import check_image_quality, check_image_quality_with_model
from prompts import (
    DETECTION_PROMPT,
    IMAGE_LABELS,
    VISION_ANALYSIS_PROMPT,
    build_explanation_prompt,
)
from providers import call_provider, call_provider_text
from scoring import merge_findings, parse_and_score, parse_detection, parse_explanation, score_from_detection


def _labels_for_images(count: int) -> List[str]:
    return IMAGE_LABELS[:count]


def _build_explanation_input(detection_findings: dict) -> str:
    """
    Keep Pass 2 prompt small by sending only fields needed
    to write patient-facing explanations.
    """
    reduced = {
        "visibility": detection_findings.get("visibility", {}),
        "observed_signs": detection_findings.get("observed_signs", []),
        "not_assessable_from_photo": detection_findings.get("not_assessable_from_photo", []),
        "scores": detection_findings.get("scores", {}),
        "confidence_breakdown": detection_findings.get("confidence_breakdown", {}),
    }
    return json.dumps(reduced, separators=(",", ":"))


def _merge_usage(
    detection_usage: dict,
    explanation_usage: Optional[dict] = None,
    quality_usage: Optional[dict] = None,
) -> dict:
    if explanation_usage is None:
        return {
            **detection_usage,
            "pipeline": "single_pass",
            "passes": {"analysis": detection_usage},
        }

    usages = [u for u in (quality_usage, detection_usage, explanation_usage) if u]
    input_tokens = sum(u.get("input_tokens") or 0 for u in usages)
    output_tokens = sum(u.get("output_tokens") or 0 for u in usages)
    passes = {}
    if quality_usage:
        passes["quality"] = quality_usage
    passes["detection"] = detection_usage
    passes["explanation"] = explanation_usage

    return {
        "raw_text": explanation_usage.get("raw_text"),
        "input_tokens": input_tokens or None,
        "output_tokens": output_tokens or None,
        "total_tokens": input_tokens + output_tokens,
        "latency_seconds": round(sum(u.get("latency_seconds", 0) for u in usages), 3),
        "pipeline": "two_pass",
        "passes": passes,
    }


def run_single_pass(
    provider: str,
    images: List[Tuple[bytes, str]],
    model: str,
) -> dict:
    usage = call_provider(provider, images, VISION_ANALYSIS_PROMPT, model=model)
    scoring_result = parse_and_score(usage["raw_text"])
    merged_usage = _merge_usage(usage)
    return {
        "scoring_result": scoring_result,
        "usage": merged_usage,
        "raw_model_output": usage["raw_text"],
        "pipeline": "single_pass",
        "quality_result": None,
        "detection_result": None,
    }


def run_two_pass(
    provider: str,
    images: List[Tuple[bytes, str]],
    model: str,
    quality_model: Optional[str] = None,
) -> dict:
    labels = _labels_for_images(len(images))
    quality_usage = None
    if provider == "gemini" and quality_model:
        quality_result, quality_usage = check_image_quality_with_model(
            provider, images, labels, quality_model
        )
    else:
        quality_result = check_image_quality(images, labels)

    if not quality_result["ok"]:
        return {
            "quality_rejected": True,
            "quality_result": quality_result,
            "quality_usage": quality_usage,
            "pipeline": "two_pass",
        }

    detection_usage = call_provider(provider, images, DETECTION_PROMPT, model=model)
    detection_result = parse_detection(detection_usage["raw_text"])

    if not detection_result["parsed_ok"]:
        scoring_result = {
            "parsed_ok": False,
            "findings": detection_result.get("findings"),
            "overall_score": None,
            "category_scores": detection_result.get("category_scores"),
            "parse_error": detection_result["parse_error"],
        }
        merged_usage = _merge_usage(detection_usage, quality_usage=quality_usage)
        return {
            "scoring_result": scoring_result,
            "usage": merged_usage,
            "raw_model_output": detection_usage["raw_text"],
            "pipeline": "two_pass",
            "quality_result": quality_result,
            "detection_result": detection_result,
            "quality_rejected": False,
        }

    detection_findings = detection_result["findings"]
    explanation_prompt = build_explanation_prompt(_build_explanation_input(detection_findings))
    explanation_usage = call_provider_text(provider, explanation_prompt, model=model)
    explanation_result = parse_explanation(explanation_usage["raw_text"])

    if explanation_result["parsed_ok"]:
        merged_findings = merge_findings(detection_findings, explanation_result["findings"])
        scoring_result = score_from_detection(merged_findings)
    else:
        scoring_result = score_from_detection(detection_findings)
        scoring_result["parse_error"] = (
            f"Explanation pass failed: {explanation_result['parse_error']}. "
            "Scores from detection pass only."
        )

    merged_usage = _merge_usage(detection_usage, explanation_usage, quality_usage)
    return {
        "scoring_result": scoring_result,
        "usage": merged_usage,
        "raw_model_output": explanation_usage["raw_text"],
        "pipeline": "two_pass",
        "quality_result": quality_result,
        "detection_result": detection_result,
        "quality_rejected": False,
    }


def run_analysis(
    provider: str,
    images: List[Tuple[bytes, str]],
    model: str,
    two_pass: bool = True,
    quality_model: Optional[str] = None,
) -> dict:
    if two_pass:
        return run_two_pass(provider, images, model, quality_model=quality_model)
    return run_single_pass(provider, images, model)

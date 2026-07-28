"""
Groq comparison pipeline - separate from the main multi-provider flow.

Pipeline:
  1. Vision capture (qwen/qwen3.6-27b) - Groq's active vision model
  2. Metrics structurer (openai/gpt-oss-20b) - converts description to scored JSON
  3. Report evaluator (openai/gpt-oss-120b) - patient-friendly report from metrics
"""

import json
from typing import List, Tuple

from image_quality import check_image_quality, resize_images_for_vision
from prompts import (
    GROQ_VISION_CAPTURE_PROMPT,
    IMAGE_LABELS,
    build_groq_metrics_prompt,
    build_groq_report_prompt,
)
from providers import call_groq, call_groq_gpt_oss_text
from scoring import merge_findings, parse_detection, parse_explanation, score_from_detection

GROQ_VISION_MODEL = "qwen/qwen3.6-27b"
GROQ_METRICS_MODEL = "openai/gpt-oss-20b"
GROQ_REPORT_MODEL = "openai/gpt-oss-120b"
MAX_VISION_DESCRIPTION_CHARS = 3500


def _labels_for_images(count: int) -> List[str]:
    return IMAGE_LABELS[:count]


def _truncate_text(text: str, max_chars: int = MAX_VISION_DESCRIPTION_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n...[truncated]"


def _merge_usage(*usages: dict) -> dict:
    input_tokens = sum(u.get("input_tokens") or 0 for u in usages)
    output_tokens = sum(u.get("output_tokens") or 0 for u in usages)
    latency = round(sum(u.get("latency_seconds") or 0 for u in usages), 3)
    return {
        "input_tokens": input_tokens or None,
        "output_tokens": output_tokens or None,
        "total_tokens": input_tokens + output_tokens,
        "latency_seconds": latency,
        "passes": {
            "vision_capture": usages[0],
            "metrics": usages[1],
            "report": usages[2],
        },
    }


def run_groq_comparison(images: List[Tuple[bytes, str]]) -> dict:
    labels = _labels_for_images(len(images))
    quality_result = check_image_quality(images, labels)
    if not quality_result["ok"]:
        return {
            "quality_rejected": True,
            "quality_result": quality_result,
            "pipeline": "groq_comparison",
        }

    # Shrink large uploads before vision call (Groq free tier is ~8k TPM).
    vision_images = resize_images_for_vision(images)

    # Step 1: Vision capture (Groq vision API)
    vision_usage = call_groq(
        vision_images,
        GROQ_VISION_CAPTURE_PROMPT,
        model=GROQ_VISION_MODEL,
        max_completion_tokens=1200,
    )
    vision_description = vision_usage["raw_text"] or ""
    metrics_input = _truncate_text(vision_description)

    # Step 2: GPT-OSS 20B - structure metrics from vision text
    # Keep max_completion_tokens low: Groq free tier rejects any single
    # request where prompt + reserved completion > ~8000 tokens.
    metrics_prompt = build_groq_metrics_prompt(metrics_input)
    metrics_usage = call_groq_gpt_oss_text(
        metrics_prompt,
        model=GROQ_METRICS_MODEL,
        max_completion_tokens=1500,
        reasoning_effort="low",
    )
    detection_result = parse_detection(metrics_usage["raw_text"])

    if not detection_result["parsed_ok"]:
        merged_usage = _merge_usage(vision_usage, metrics_usage, {"raw_text": "", "input_tokens": 0, "output_tokens": 0, "latency_seconds": 0})
        return {
            "quality_rejected": False,
            "quality_result": quality_result,
            "pipeline": "groq_comparison",
            "vision_model": GROQ_VISION_MODEL,
            "metrics_model": GROQ_METRICS_MODEL,
            "report_model": GROQ_REPORT_MODEL,
            "vision_description": vision_description,
            "scoring_result": {
                "parsed_ok": False,
                "findings": detection_result.get("findings"),
                "overall_score": None,
                "category_scores": detection_result.get("category_scores"),
                "parse_error": detection_result["parse_error"],
            },
            "usage": merged_usage,
            "raw_model_output": metrics_usage["raw_text"],
        }

    detection_findings = detection_result["findings"]

    # Step 3: GPT OSS 120B - patient report from metrics
    report_prompt = build_groq_report_prompt(json.dumps(detection_findings, separators=(",", ":")))
    report_usage = call_groq_gpt_oss_text(
        report_prompt,
        model=GROQ_REPORT_MODEL,
        max_completion_tokens=1800,
        reasoning_effort="low",
    )
    explanation_result = parse_explanation(report_usage["raw_text"])

    if explanation_result["parsed_ok"]:
        merged_findings = merge_findings(detection_findings, explanation_result["findings"])
        scoring_result = score_from_detection(merged_findings)
    else:
        scoring_result = score_from_detection(detection_findings)
        scoring_result["parse_error"] = (
            f"Report pass failed: {explanation_result['parse_error']}. "
            "Scores from metrics pass only."
        )

    merged_usage = _merge_usage(vision_usage, metrics_usage, report_usage)

    return {
        "quality_rejected": False,
        "quality_result": quality_result,
        "pipeline": "groq_comparison",
        "vision_model": GROQ_VISION_MODEL,
        "metrics_model": GROQ_METRICS_MODEL,
        "report_model": GROQ_REPORT_MODEL,
        "vision_description": vision_description,
        "scoring_result": scoring_result,
        "usage": merged_usage,
        "raw_model_output": report_usage["raw_text"],
        "detection_result": detection_result,
    }

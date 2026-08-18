"""
report.py

Turns the parsed findings + computed score into the plain-text report the
user sees. Pure string formatting, no AI calls, no business logic - 
scoring.py already did that.
"""

DISCLAIMER = (
    "This is a preliminary AI-generated visual assessment based only on the "
    "uploaded photo. It is not a diagnosis. A clinical examination, and "
    "X-rays where appropriate, are required to confirm any findings."
)



def get_treatment_recommendations(findings: dict) -> dict:
    if not isinstance(findings, dict):
        findings = {}
    
    recs = findings.get("treatment_recommendations")
    if isinstance(recs, dict) and "primary" in recs:
        p = recs["primary"] or {}
        if isinstance(p, dict) and p.get("title"):
            return recs
            
    # Fallback to treatment_roadmap
    roadmap = findings.get("treatment_roadmap") or []
    if isinstance(roadmap, list) and roadmap:
        return {
            "primary": {
                "title": "Recommended Treatment Pathway",
                "description": "A customized treatment plan based on your preliminary findings.",
                "rationale": "Indicated to address the identified visual concerns and restore optimal dental health.",
                "steps": [str(s) for s in roadmap if str(s).strip()]
            },
            "additional": []
        }
        
    return {
        "primary": {
            "title": "Routine Dental Consultation",
            "description": "A complete professional oral examination and cleaning.",
            "rationale": "Recommended to confirm these preliminary findings and formulate a clinical plan.",
            "steps": [
                "Book an appointment with a dentist.",
                "Undergo a visual and radiographic examination."
            ]
        },
        "additional": []
    }


def build_report(
    provider: str,
    model_name: str,
    scoring_result: dict,
    usage: dict,
    raw_text: str,
    images_used: int = 1,
    pipeline: str = "single_pass",
    quality_result: dict = None,
    detection_result: dict = None,
) -> str:
    lines = []
    lines.append("=" * 50)
    lines.append("VIRTUAL SMILE ASSESSMENT - PRELIMINARY REPORT")
    lines.append("=" * 50)
    lines.append(f"Provider / Model: {provider} ({model_name})")
    lines.append(f"Pipeline: {pipeline}")
    lines.append(f"Images analyzed: {images_used}")
    lines.append("")

    if not scoring_result["parsed_ok"]:
        lines.append("STATUS: Could not generate a scored report.")
        lines.append(f"Reason: {scoring_result['parse_error']}")
        lines.append("")
        lines.append("Raw model output is included below for debugging:")
        lines.append("-" * 50)
        lines.append(raw_text if raw_text else "(model returned empty content)")
        lines.append("-" * 50)
        lines.append("")
        _append_usage(lines, usage)
        return "\n".join(lines)

    findings = scoring_result["findings"]
    category_scores = scoring_result["category_scores"]

    lines.append(f"OVERALL SMILE SCORE: {scoring_result['overall_score']} / 100")
    lines.append("")
    lines.append("Category breakdown:")
    for category, score in category_scores.items():
        label = category.replace("_", " ").title()
        lines.append(f" - {label}: {score} / 100")
    lines.append("")

    observed_signs = findings.get("observed_signs", [])
    if observed_signs:
        lines.append("-" * 50)
        lines.append("VISUAL EVIDENCE (from photo inspection)")
        lines.append("-" * 50)
        for sign in observed_signs:
            if not isinstance(sign, dict):
                continue
            feature = sign.get("visible_feature", "")
            location = sign.get("location", "")
            views = sign.get("evidence_views", [])
            strength = sign.get("evidence_strength", "")
            conf = sign.get("confidence")
            label = sign.get("concern_label", sign.get("sign", "")).replace("_", " ")
            lines.append(f" - {label}")
            if feature:
                lines.append(f"      Seen: {feature}")
            if location:
                lines.append(f"      Location: {location}")
            if views:
                lines.append(f"      Views: {', '.join(views)}")
            if strength:
                lines.append(f"      Strength: {strength}")
            if conf is not None:
                lines.append(f"      Confidence: {conf}")
        lines.append("")

    not_assessable = findings.get("not_assessable_from_photo", [])
    if not_assessable:
        lines.append("Cannot assess from photos alone:")
        for item in not_assessable:
            lines.append(f" - {item.replace('_', ' ')}")
        lines.append("")

    lines.append("-" * 50)
    lines.append("REPORT")
    lines.append("-" * 50)
    lines.append("")

    concerns = findings.get("visible_concerns", [])
    concern_details = findings.get("concern_details", [])
    details_by_label = {d.get("concern"): d for d in concern_details if isinstance(d, dict)}

    lines.append("1. Visible concerns identified")
    if concerns:
        for c in concerns:
            lines.append(f" - {c.replace('_', ' ')}")
    else:
        lines.append("   No obvious concerns identified in this photo.")
    lines.append("")

    lines.append("2. Why these issues may be occurring")
    if concerns:
        for c in concerns:
            detail = details_by_label.get(c)
            cause = detail.get("likely_cause") if detail else None
            label = c.replace("_", " ")
            if cause:
                lines.append(f" - {label}: {cause}")
            else:
                lines.append(f" - {label}: (no explanation provided by model)")
    else:
        lines.append("   Not applicable - no concerns identified.")
    lines.append("")

    lines.append("3. Possible treatment options")
    if concerns:
        for c in concerns:
            detail = details_by_label.get(c)
            options = detail.get("treatment_options") if detail else None
            label = c.replace("_", " ")
            if options:
                lines.append(f" - {label}: {', '.join(options)}")
            else:
                lines.append(f" - {label}: (no options provided by model)")
    else:
        lines.append("   Not applicable - no concerns identified.")
    lines.append("")

    priority_order = findings.get("priority_order", [])
    lines.append("4. What should be addressed first")
    if priority_order:
        for i, c in enumerate(priority_order, start=1):
            lines.append(f"   {i}. {c.replace('_', ' ')}")
    elif concerns:
        lines.append("   (model did not provide a priority order)")
    else:
        lines.append("   Not applicable - no concerns identified.")
    lines.append("")

    roadmap = findings.get("treatment_roadmap", [])
    lines.append("5. Suggested treatment roadmap")
    if roadmap:
        for step in roadmap:
            lines.append(f" - {step}")
    else:
        lines.append("   (model did not provide a roadmap)")
    lines.append("")

    # Output structured treatment recommendations
    recs = get_treatment_recommendations(findings)
    primary = recs.get("primary") or {}
    additional = recs.get("additional") or []

    lines.append("6. Suggested Treatment Recommendations")
    lines.append(f"   Primary Recommendation: {primary.get('title', 'N/A')}")
    lines.append(f"     Description: {primary.get('description', 'N/A')}")
    lines.append(f"     Rationale: {primary.get('rationale', 'N/A')}")
    if primary.get("steps"):
        lines.append("     Steps:")
        for step in primary["steps"]:
            lines.append(f"       - {step}")
            
    if additional:
        lines.append("   Additional Recommendations:")
        for idx, add in enumerate(additional, 1):
            lines.append(f"     {idx}. {add.get('title', 'N/A')}")
            lines.append(f"        Description: {add.get('description', 'N/A')}")
            lines.append(f"        Rationale: {add.get('rationale', 'N/A')}")
            if add.get("steps"):
                lines.append("        Steps:")
                for step in add["steps"]:
                    lines.append(f"          - {step}")
    lines.append("")

    priority = findings.get("priority_level", "not specified")
    lines.append(f"Overall priority level: {priority}")

    confidence = findings.get("confidence")
    if confidence is not None:
        lines.append(f"Model confidence: {confidence}")

    confidence_breakdown = findings.get("confidence_breakdown")
    if confidence_breakdown:
        lines.append("Confidence breakdown:")
        for key, value in confidence_breakdown.items():
            lines.append(f" - {key.replace('_', ' ')}: {value}")
    lines.append("")

    notes = findings.get("notes")
    if notes:
        lines.append("Summary notes:")
        lines.append(f"  {notes}")
        lines.append("")

    lines.append("-" * 50)
    lines.append(DISCLAIMER)
    lines.append("-" * 50)
    lines.append("")
    _append_usage(lines, usage)
    return "\n".join(lines)


def _append_usage(lines: list, usage: dict) -> None:
    lines.append("TOKEN USAGE (this request)")
    lines.append(f"  Input tokens:  {usage.get('input_tokens')}")
    lines.append(f"  Output tokens: {usage.get('output_tokens')}")
    lines.append(f"  Total tokens:  {usage.get('total_tokens')}")
    lines.append(f"  Latency:       {usage.get('latency_seconds')}s")

    passes = usage.get("passes")
    if passes:
        lines.append("")
        lines.append("Per-pass breakdown:")
        for pass_name, pass_usage in passes.items():
            lines.append(
                f"  {pass_name}: "
                f"in={pass_usage.get('input_tokens')} "
                f"out={pass_usage.get('output_tokens')} "
                f"latency={pass_usage.get('latency_seconds')}s"
            )


def build_groq_comparison_report(
    scoring_result: dict,
    usage: dict,
    raw_text: str,
    images_used: int,
    vision_model: str,
    metrics_model: str,
    report_model: str,
    vision_description: str,
) -> str:
    lines = []
    lines.append("=" * 50)
    lines.append("GROQ COMPARISON PIPELINE - PRELIMINARY REPORT")
    lines.append("=" * 50)
    lines.append(f"Pipeline: groq_comparison (3-step)")
    lines.append(f"  1. Vision capture: {vision_model}")
    lines.append(f"  2. Metrics structurer: {metrics_model}")
    lines.append(f"  3. Report evaluator: {report_model}")
    lines.append(f"Images analyzed: {images_used}")
    lines.append("")

    if vision_description:
        lines.append("-" * 50)
        lines.append("VISION CAPTURE (raw description from images)")
        lines.append("-" * 50)
        lines.append(vision_description)
        lines.append("")

    report_body = build_report(
        provider="groq",
        model_name=report_model,
        scoring_result=scoring_result,
        usage=usage,
        raw_text=raw_text,
        images_used=images_used,
        pipeline="groq_comparison",
    )
    # Skip duplicate header from build_report - append from score section onward
    score_marker = "OVERALL SMILE SCORE:"
    if score_marker in report_body:
        lines.append(report_body[report_body.index(score_marker):])
    else:
        lines.append(report_body)

    return "\n".join(lines)

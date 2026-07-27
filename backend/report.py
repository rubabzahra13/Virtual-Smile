"""
report.py

Turns the parsed findings + computed score into the plain-text report the
user sees. Pure string formatting, no AI calls, no business logic —
scoring.py already did that.
"""

DISCLAIMER = (
    "This is a preliminary AI-generated visual assessment based only on the "
    "uploaded photo. It is not a diagnosis. A clinical examination, and "
    "X-rays where appropriate, are required to confirm any findings."
)


def build_report(provider: str, model_name: str, scoring_result: dict, usage: dict, raw_text: str, images_used: int = 1) -> str:
    lines = []
    lines.append("=" * 50)
    lines.append("VIRTUAL SMILE ASSESSMENT — PRELIMINARY REPORT")
    lines.append("=" * 50)
    lines.append(f"Provider / Model: {provider} ({model_name})")
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
        lines.append("TOKEN USAGE (this request)")
        lines.append(f"  Input tokens:  {usage['input_tokens']}")
        lines.append(f"  Output tokens: {usage['output_tokens']}")
        lines.append(f"  Total tokens:  {usage['total_tokens']}")
        lines.append(f"  Latency:       {usage['latency_seconds']}s")
        return "\n".join(lines)

    findings = scoring_result["findings"]
    category_scores = scoring_result["category_scores"]

    lines.append(f"OVERALL SMILE SCORE: {scoring_result['overall_score']} / 100")
    lines.append("")
    lines.append("Category breakdown:")
    for category, score in category_scores.items():
        label = category.replace("_", " ").title()
        lines.append(f"  - {label}: {score} / 100")
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
            lines.append(f"   - {c.replace('_', ' ')}")
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
                lines.append(f"   - {label}: {cause}")
            else:
                lines.append(f"   - {label}: (no explanation provided by model)")
    else:
        lines.append("   Not applicable — no concerns identified.")
    lines.append("")

    lines.append("3. Possible treatment options")
    if concerns:
        for c in concerns:
            detail = details_by_label.get(c)
            options = detail.get("treatment_options") if detail else None
            label = c.replace("_", " ")
            if options:
                lines.append(f"   - {label}: {', '.join(options)}")
            else:
                lines.append(f"   - {label}: (no options provided by model)")
    else:
        lines.append("   Not applicable — no concerns identified.")
    lines.append("")

    priority_order = findings.get("priority_order", [])
    lines.append("4. What should be addressed first")
    if priority_order:
        for i, c in enumerate(priority_order, start=1):
            lines.append(f"   {i}. {c.replace('_', ' ')}")
    elif concerns:
        lines.append("   (model did not provide a priority order)")
    else:
        lines.append("   Not applicable — no concerns identified.")
    lines.append("")

    roadmap = findings.get("treatment_roadmap", [])
    lines.append("5. Suggested treatment roadmap")
    if roadmap:
        for step in roadmap:
            lines.append(f"   - {step}")
    else:
        lines.append("   (model did not provide a roadmap)")
    lines.append("")

    priority = findings.get("priority_level", "not specified")
    lines.append(f"Overall priority level: {priority}")

    confidence = findings.get("confidence")
    if confidence is not None:
        lines.append(f"Model confidence: {confidence}")
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
    lines.append("TOKEN USAGE (this request)")
    lines.append(f"  Input tokens:  {usage['input_tokens']}")
    lines.append(f"  Output tokens: {usage['output_tokens']}")
    lines.append(f"  Total tokens:  {usage['total_tokens']}")
    lines.append(f"  Latency:       {usage['latency_seconds']}s")

    return "\n".join(lines)
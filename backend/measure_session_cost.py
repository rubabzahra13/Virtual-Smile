"""
Live max-session Gemini usage measurement.

One full patient session:
  3 images -> quality + detection + explanation
  treatment simulation
  5 chat messages

Tokens come from Gemini usage_metadata (not estimates).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from patient_features import (  # noqa: E402
    _prepare_image_for_edit,
    _treatment_edit_brief,
    build_chat_prompt,
)
from report import build_report  # noqa: E402

ASSETS = ROOT / "frontend" / "assets"
OUT_PATH = ROOT / "backend" / "session_cost_measured.json"

# Official paid-tier rates (USD per 1M tokens) — ai.google.dev/gemini-api/docs/pricing
RATES = {
    "gemini-3.5-flash-lite": {
        "input": 0.30,
        "output": 2.50,  # includes thinking
    },
    "gemini-3.1-flash-image": {
        "input": 0.50,
        "output_text": 3.00,
        "output_image": 60.00,
    },
    "gemini-3.1-flash-image-preview": {
        "input": 0.50,
        "output_text": 3.00,
        "output_image": 60.00,
    },
    "gemini-3.1-flash-lite-image": {
        "input": 0.25,
        "output_text": 1.50,
        "output_image": 30.00,
    },
}


def _usage_dict(usage) -> dict:
    if usage is None:
        return {}
    fields = [
        "prompt_token_count",
        "candidates_token_count",
        "thoughts_token_count",
        "total_token_count",
        "cached_content_token_count",
        "tool_use_prompt_token_count",
    ]
    data = {}
    for name in fields:
        val = getattr(usage, name, None)
        if val is not None:
            data[name] = int(val)

    for attr in ("prompt_tokens_details", "candidates_tokens_details", "cache_tokens_details"):
        details = getattr(usage, attr, None)
        if not details:
            continue
        rows = []
        for item in details:
            rows.append(
                {
                    "modality": str(getattr(item, "modality", None)),
                    "token_count": getattr(item, "token_count", None),
                }
            )
        data[attr] = rows
    return data


def _billable_text_io(meta: dict) -> tuple[int, int]:
    inp = int(meta.get("prompt_token_count") or 0)
    candidates = int(meta.get("candidates_token_count") or 0)
    thoughts = int(meta.get("thoughts_token_count") or 0)
    out = candidates + thoughts
    if out == 0 and meta.get("total_token_count"):
        out = max(0, int(meta["total_token_count"]) - inp)
    return inp, out


def _image_modality_tokens(details) -> tuple[int, int]:
    text = 0
    image = 0
    for row in details or []:
        modality = (row.get("modality") or "").upper()
        count = int(row.get("token_count") or 0)
        if "IMAGE" in modality:
            image += count
        else:
            text += count
    return text, image


def cost_flash_lite(inp: int, out: int) -> float:
    r = RATES["gemini-3.5-flash-lite"]
    return (inp / 1_000_000) * r["input"] + (out / 1_000_000) * r["output"]


def cost_flash_image(meta: dict, model: str = "gemini-3.1-flash-image") -> dict:
    r = RATES.get(model) or RATES["gemini-3.1-flash-image"]
    inp = int(meta.get("prompt_token_count") or 0)
    cand_text, cand_image = _image_modality_tokens(meta.get("candidates_tokens_details"))
    thoughts = int(meta.get("thoughts_token_count") or 0)
    candidates = int(meta.get("candidates_token_count") or 0)
    if cand_text == 0 and cand_image == 0 and candidates:
        cand_image = candidates
    # Residual candidates not tagged as IMAGE are billed at text/thinking rate.
    residual = max(0, candidates - cand_image - cand_text)
    text_out = cand_text + thoughts + residual
    image_out = cand_image
    dollars = (
        (inp / 1_000_000) * r["input"]
        + (text_out / 1_000_000) * r["output_text"]
        + (image_out / 1_000_000) * r["output_image"]
    )
    return {
        "input_tokens": inp,
        "text_output_tokens": text_out,
        "image_output_tokens": image_out,
        "cost_usd": round(dollars, 8),
    }


def load_images():
    paths = [
        ASSETS / "example-front.png",
        ASSETS / "example-left.png",
        ASSETS / "example-right.png",
    ]
    images = []
    for p in paths:
        data = p.read_bytes()
        images.append((data, "image/png"))
        print(f"loaded {p.name}: {len(data)} bytes")
    return images


def run_gemini(model: str, contents) -> tuple[str, dict, float]:
    from google import genai

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    start = time.time()
    response = client.models.generate_content(model=model, contents=contents)
    latency = time.time() - start
    meta = _usage_dict(getattr(response, "usage_metadata", None))
    text = ""
    try:
        text = response.text or ""
    except Exception:
        text = ""
    return text, meta, round(latency, 3)


def measure_analysis(images) -> tuple[list[dict], dict, str]:
    from google.genai import types
    from prompts import (
        DETECTION_PROMPT,
        IMAGE_LABELS,
        build_explanation_prompt,
        build_image_quality_prompt,
    )
    from analysis import _build_explanation_input
    from image_quality import parse_quality_response
    from scoring import merge_findings, parse_detection, parse_explanation, score_from_detection

    model = "gemini-3.5-flash-lite"
    image_parts = [types.Part.from_bytes(data=b, mime_type=m) for b, m in images]
    labels = IMAGE_LABELS[: len(images)]
    steps = []

    quality_prompt = build_image_quality_prompt(labels)
    q_text, q_meta, q_lat = run_gemini(model, [quality_prompt, *image_parts])
    q_in, q_out = _billable_text_io(q_meta)
    quality_result = parse_quality_response(q_text, labels)
    steps.append(
        {
            "step": "quality",
            "model": model,
            "latency_seconds": q_lat,
            "usage_metadata": q_meta,
            "input_tokens": q_in,
            "output_tokens": q_out,
            "response_chars": len(q_text),
            "cost_usd": round(cost_flash_lite(q_in, q_out), 8),
            "source": "raw_usage_metadata",
        }
    )
    if not quality_result.get("ok", True):
        raise RuntimeError(f"Quality rejected: {quality_result}")

    d_text, d_meta, d_lat = run_gemini(model, [DETECTION_PROMPT, *image_parts])
    d_in, d_out = _billable_text_io(d_meta)
    detection_result = parse_detection(d_text)
    steps.append(
        {
            "step": "detection",
            "model": model,
            "latency_seconds": d_lat,
            "usage_metadata": d_meta,
            "input_tokens": d_in,
            "output_tokens": d_out,
            "response_chars": len(d_text),
            "cost_usd": round(cost_flash_lite(d_in, d_out), 8),
            "source": "raw_usage_metadata",
        }
    )
    if not detection_result.get("parsed_ok"):
        raise RuntimeError(f"Detection parse failed: {detection_result.get('parse_error')}")

    detection_findings = detection_result["findings"]
    explanation_prompt = build_explanation_prompt(_build_explanation_input(detection_findings))
    e_text, e_meta, e_lat = run_gemini(model, explanation_prompt)
    e_in, e_out = _billable_text_io(e_meta)
    explanation_result = parse_explanation(e_text)
    steps.append(
        {
            "step": "explanation",
            "model": model,
            "latency_seconds": e_lat,
            "usage_metadata": e_meta,
            "input_tokens": e_in,
            "output_tokens": e_out,
            "response_chars": len(e_text),
            "cost_usd": round(cost_flash_lite(e_in, e_out), 8),
            "source": "raw_usage_metadata",
        }
    )

    if explanation_result.get("parsed_ok"):
        merged_findings = merge_findings(detection_findings, explanation_result["findings"])
        scoring_result = score_from_detection(merged_findings)
    else:
        scoring_result = score_from_detection(detection_findings)

    usage = {
        "input_tokens": q_in + d_in + e_in,
        "output_tokens": q_out + d_out + e_out,
        "total_tokens": q_in + d_in + e_in + q_out + d_out + e_out,
        "latency_seconds": round(q_lat + d_lat + e_lat, 3),
        "passes": {
            "quality": {"input_tokens": q_in, "output_tokens": q_out, "latency_seconds": q_lat},
            "detection": {"input_tokens": d_in, "output_tokens": d_out, "latency_seconds": d_lat},
            "explanation": {"input_tokens": e_in, "output_tokens": e_out, "latency_seconds": e_lat},
        },
    }

    report_text = build_report(
        provider="gemini",
        model_name=model,
        scoring_result=scoring_result,
        usage=usage,
        raw_text=e_text,
        images_used=len(images),
        pipeline="two_pass_measured",
        quality_result=quality_result,
        detection_result=detection_result,
    )
    return steps, scoring_result, report_text


def measure_simulation(front_bytes: bytes, report_text: str, findings_json: str | None) -> dict:
    from google import genai
    from google.genai import types

    model = os.getenv("GEMINI_SIMULATION_MODEL", "gemini-3.1-flash-image")
    edit_bytes, edit_mime = _prepare_image_for_edit(front_bytes, "image/png")
    treatment_brief = _treatment_edit_brief(report_text, findings_json)

    prompt = f"""TASK: Inpaint/edit the ATTACHED photo only.
You must return a modified version of THIS exact image.
You are NOT allowed to generate a new mouth, new face crop, or stock-photo smile.

SUCCESS LOOKS LIKE:
- A viewer can instantly tell it is the same person and same photo.
- Lips, philtrum, skin pores, wrinkles, freckles, stubble, and lighting match the original.
- Tooth count, tooth outlines, midline, and gum scalloping still match.
- Existing crowns/veneers/fillings remain visible unless the brief says otherwise.
- Changes are mild clinical improvements from the treatment brief (cleaning, slight whitening, tiny alignment).

FAILURE LOOKS LIKE (FORBIDDEN):
- A different mouth or different lip shape.
- Airbrushed skin / beauty-filter face.
- Perfect uniform "Hollywood veneers" smile.
- All teeth remade into identical bright-white rectangles.
- Removing a distinctive crown/veneer and replacing the whole smile.
- Changing camera angle, crop, mouth opening amount, or background.

HARD CONSTRAINTS:
1. Preserve identity at pixel level: same framing, pose, mouth opening, camera distance.
2. Preserve anatomy: same lips, same gumline geometry, same number of teeth.
3. Preserve unique dental landmarks (chips, rotations, existing restorations) except tiny allowed edits.
4. Intensity limit: whitening max ~1-2 shades; alignment max tiny nudge; no full orthodontic redesign.
5. Natural enamel only - never pure white, never plastic gloss, never catalogue smile.
6. No text, logos, braces overlays, watermarks, or extra people.
7. If any instruction conflicts with identity preservation, KEEP THE ORIGINAL and make a milder edit.

TREATMENT BRIEF (apply only these, subtly):
{treatment_brief}

PROCESS:
1. Lock all non-tooth regions (lips, skin, background) as unchanged.
2. Edit only tooth surfaces / mild gum tone where the brief requires.
3. Re-check that the result still looks like the original photo with treatment, not a replacement smile.

OUTPUT: one edited image of the SAME attached smile after those treatments.
"""

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    start = time.time()
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=edit_bytes, mime_type=edit_mime),
            prompt,
        ],
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    latency = round(time.time() - start, 3)
    meta = _usage_dict(getattr(response, "usage_metadata", None))
    got_image = False
    parts = list(getattr(response, "parts", []) or [])
    if not parts and getattr(response, "candidates", None):
        parts = list(getattr(response.candidates[0].content, "parts", []) or [])
    for part in parts:
        if getattr(part, "inline_data", None) is not None:
            got_image = True
            break

    priced = cost_flash_image(meta, model=model)
    return {
        "step": "simulation",
        "model": model,
        "latency_seconds": latency,
        "got_image": got_image,
        "edit_image_bytes": len(edit_bytes),
        "usage_metadata": meta,
        "input_tokens": priced["input_tokens"],
        "text_output_tokens": priced["text_output_tokens"],
        "image_output_tokens": priced["image_output_tokens"],
        "output_tokens": priced["text_output_tokens"] + priced["image_output_tokens"],
        "cost_usd": priced["cost_usd"],
        "source": "raw_usage_metadata",
    }


def measure_chat(question: str, report_text: str, overall_score) -> dict:
    model = os.getenv("PATIENT_CHAT_MODEL", "gemini-3.5-flash-lite")
    prompt = build_chat_prompt(question, report_text, overall_score)
    text, meta, latency = run_gemini(model, prompt)
    inp, out = _billable_text_io(meta)
    return {
        "step": "chat",
        "model": model,
        "question": question,
        "answer_chars": len(text.strip()),
        "latency_seconds": latency,
        "usage_metadata": meta,
        "input_tokens": inp,
        "output_tokens": out,
        "cost_usd": round(cost_flash_lite(inp, out), 8),
        "source": "raw_usage_metadata",
    }


def main():
    if not os.getenv("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY missing")

    images = load_images()

    print("\n=== 1) Analysis (quality + detection + explanation) ===")
    analysis_rows, scoring, report_text = measure_analysis(images)
    for row in analysis_rows:
        print(
            f"  {row['step']}: in={row['input_tokens']} out={row['output_tokens']} "
            f"cost=${row['cost_usd']:.6f} meta={row['usage_metadata']}"
        )
    print(f"score={scoring.get('overall_score')} report_chars={len(report_text)}")

    print("\n=== 2) Treatment simulation ===")
    findings_json = json.dumps(scoring.get("findings") or {})
    sim = measure_simulation(images[0][0], report_text, findings_json)
    print(
        f"  sim: got_image={sim['got_image']} in={sim['input_tokens']} "
        f"text_out={sim['text_output_tokens']} image_out={sim['image_output_tokens']} "
        f"cost=${sim['cost_usd']:.6f} meta={sim['usage_metadata']}"
    )

    print("\n=== 3) Five chatbot messages ===")
    questions = [
        "What are my main dental concerns from this report?",
        "Do I need braces or clear aligners based on these findings?",
        "How urgent is gum or plaque care according to the assessment?",
        "What whitening options make sense for my tooth color findings?",
        "What should I ask the dentist at my consultation after this AI report?",
    ]
    chat_rows = []
    for q in questions:
        row = measure_chat(q, report_text, scoring.get("overall_score"))
        chat_rows.append(row)
        print(
            f"  chat: in={row['input_tokens']} out={row['output_tokens']} "
            f"cost=${row['cost_usd']:.6f} q={q[:48]}..."
        )

    steps = analysis_rows + [sim] + chat_rows
    total_cost = sum(float(s["cost_usd"]) for s in steps)
    total_in = sum(int(s.get("input_tokens") or 0) for s in steps)
    total_out = sum(int(s.get("output_tokens") or 0) for s in steps)

    monthly = {
        "100_max_sessions": round(total_cost * 100, 4),
        "500_max_sessions": round(total_cost * 500, 4),
        "1000_max_sessions": round(total_cost * 1000, 4),
        "5000_max_sessions": round(total_cost * 5000, 4),
        "10000_max_sessions": round(total_cost * 10000, 4),
    }

    # Cost formula steps for transparency
    formula_steps = []
    for s in steps:
        if s["step"] == "simulation":
            formula_steps.append(
                {
                    "step": s["step"],
                    "formula": (
                        f"({s['input_tokens']}/1e6)*0.50 + "
                        f"({s['text_output_tokens']}/1e6)*3.00 + "
                        f"({s['image_output_tokens']}/1e6)*60.00"
                    ),
                    "cost_usd": s["cost_usd"],
                }
            )
        else:
            formula_steps.append(
                {
                    "step": s["step"] if s["step"] != "chat" else f"chat:{s['question'][:40]}",
                    "formula": f"({s['input_tokens']}/1e6)*0.30 + ({s['output_tokens']}/1e6)*2.50",
                    "cost_usd": s["cost_usd"],
                }
            )

    payload = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scenario": {
            "images": 3,
            "image_files": ["example-front.png", "example-left.png", "example-right.png"],
            "analysis_passes": ["quality", "detection", "explanation"],
            "simulation": True,
            "chat_messages": 5,
            "total_llm_calls": len(steps),
        },
        "pricing_source": "https://ai.google.dev/gemini-api/docs/pricing",
        "rates_usd_per_1m": RATES,
        "overall_score": scoring.get("overall_score"),
        "report_chars": len(report_text),
        "steps": steps,
        "formula_steps": formula_steps,
        "session_totals": {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "cost_usd": round(total_cost, 8),
        },
        "monthly_projections_usd": monthly,
        "notes": [
            "Tokens are from Gemini usage_metadata on live API calls.",
            "Flash-Lite billable output = candidates_token_count + thoughts_token_count.",
            "Image model: input $0.50/1M, text+thinking $3/1M, image output $60/1M.",
            "Images used are the app example front/left/right photos (max upload case).",
            "Monthly figures assume every session is this max path (3 images + sim + 5 chats).",
        ],
    }

    OUT_PATH.write_text(json.dumps(payload, indent=2))
    print("\n=== SESSION TOTAL ===")
    print(json.dumps(payload["session_totals"], indent=2))
    print("monthly:", json.dumps(monthly, indent=2))
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

"""
Patient-facing helpers: report chat and illustrative smile simulation.
"""

import base64
import io
import json
import os
import time
import urllib.error
from typing import Optional
from language_utils import detect_chat_language


def build_chat_prompt(
    question: str,
    report_text: str,
    overall_score: Optional[int],
    history: Optional[list] = None,
    target_lang: Optional[str] = None,
) -> str:
    if not target_lang:
        target_lang = detect_chat_language(question)

    score_line = f"Overall Smile Score: {overall_score}/100\n" if overall_score is not None else ""

    history_block = ""
    if history:
        lines = []
        for turn in history[-10:]:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role") or "").strip().lower()
            content = str(turn.get("content") or "").strip()
            if not content:
                continue
            label = "Patient" if role in ("user", "patient") else "Assistant"
            lines.append(f"{label}: {content[:1200]}")
        if lines:
            history_block = (
                "\nPrior conversation (use for continuity; do not invent new findings):\n"
                + "\n".join(lines)
                + "\n"
            )

    if target_lang == "ENGLISH":
        lang_instruction = (
            "CRITICAL RESPONSE LANGUAGE REQUIREMENT:\n"
            "- You MUST respond strictly in plain ENGLISH.\n"
            "- Do NOT translate into Urdu or Roman Urdu.\n"
            "- Do NOT use Hindi script (Devanagari) or Urdu script.\n"
        )
    else:
        lang_instruction = (
            "CRITICAL RESPONSE LANGUAGE REQUIREMENT:\n"
            "- You MUST respond strictly in ROMAN URDU (Urdu written using standard English/Latin alphabets, e.g. 'Aap ko registration complete karni hogi...').\n"
            "- Do NOT respond in English language.\n"
            "- Do NOT write in Urdu script or Hindi (Devanagari) script.\n"
        )

    return f"""You are a friendly dental clinic assistant for The Global Dentist.

{lang_instruction}
ABSOLUTE SCRIPT RESTRICTION:
- NEVER output Hindi / Devanagari script (e.g. हिन्दी, नमस्कार, आदि).
- NEVER output Urdu / Arabic script (e.g. اردو, مجھے, etc.).
- Supported output characters are ONLY standard English / Latin alphabet letters.

Answer the patient's question using ONLY the assessment report below, and the prior conversation if provided.
Do not invent new findings.
Refer back to earlier questions and your previous answers when relevant so the chat feels continuous.
Always remind them this is a preliminary AI assessment, not a diagnosis, when giving treatment advice.
Keep answers concise (3-6 short sentences).

{score_line}
Assessment report:
{report_text[:6000]}
{history_block}
Patient question: {question}
"""


def _parse_findings(findings_json: Optional[str]) -> Optional[dict]:
    if not findings_json:
        return None
    try:
        data = json.loads(findings_json)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _edit_profile(
    findings_json: Optional[str] = None,
    report_text: Optional[str] = None,
) -> dict:
    """Local edit intensities from findings. Conservative but visibly cleaner."""
    profile = {
        "whiten": 0.55,
        "clean": 0.68,
        "brighten": 0.14,
    }

    blob_parts = []
    findings = _parse_findings(findings_json)
    if findings:
        for concern in findings.get("visible_concerns") or []:
            blob_parts.append(str(concern))
        for sign in findings.get("observed_signs") or []:
            if isinstance(sign, dict):
                blob_parts.append(str(sign.get("sign", "")))
                blob_parts.append(str(sign.get("concern_label", "")))
                blob_parts.append(str(sign.get("visible_feature", "")))
    if report_text:
        blob_parts.append(report_text.lower())
    blob = " ".join(blob_parts).lower()

    if any(t in blob for t in ("stain", "colour", "color", "discolor", "yellow")):
        profile["whiten"] = 0.68
        profile["brighten"] = 0.12
        profile["clean"] = max(profile["clean"], 0.55)
    if any(t in blob for t in ("plaque", "calculus", "tartar", "deposit", "brown")):
        profile["clean"] = 0.85
        profile["whiten"] = max(profile["whiten"], 0.48)
    return profile


def _clamp(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _tooth_mask_and_edit(image, profile: dict):
    """
    Sharp, texture-preserving tooth clean/whiten on the original pixels.
    - No banner overlay
    - No heavy blur
    - Does not invent teeth in gaps
    - Keeps enamel texture (adjust color/luma, do not paint flat white)
    """
    from PIL import Image, ImageFilter

    width, height = image.size
    src = image.load()

    # 1) Score tooth-likeness per pixel (raw), excluding lips/gums/oral cavity.
    score_img = Image.new("L", (width, height), 0)
    sp = score_img.load()

    x0, x1 = int(width * 0.04), int(width * 0.96)
    y0, y1 = int(height * 0.08), int(height * 0.95)

    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b = src[x, y]
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            mx = max(r, g, b)
            mn = min(r, g, b)
            sat = (mx - mn) / float(mx or 1)
            rg = (r + g) * 0.5

            # Oral cavity / missing-tooth hole: very dark, low chroma
            if luma < 48 and sat < 0.28:
                continue
            # Lips
            if r > g + 26 and r > b + 28 and r > 105 and (r - g) > 18:
                continue
            # Gums / mucosa (red-pink, not yellow-brown)
            if (r - g) > 26 and (r - b) > 26 and r > 90 and g < 135 and luma < 165:
                continue

            yellow = max(0.0, (rg - b) / 90.0)
            brown = max(0.0, (r - b) / 70.0) * max(0.0, (g - b + 8) / 70.0)

            is_enamel = luma >= 118 and sat <= 0.48 and abs(r - g) < 48 and b > 55
            is_stained = luma >= 78 and yellow > 0.12 and sat < 0.6 and r > 85 and g > 65
            is_tartar = (
                40 <= luma <= 175
                and brown > 0.18
                and r > 50
                and g > 35
                and (r - b) > 14
            )

            if is_enamel or is_stained or is_tartar:
                # Stronger score on stains/tartar so cleaning focuses there.
                base = 170
                if is_stained:
                    base = 210
                if is_tartar:
                    base = 230
                sp[x, y] = min(255, base)

    # Tiny feather only (sharp result). Avoid large Gaussian blur that softens the whole after image.
    mask = score_img.filter(ImageFilter.GaussianBlur(radius=1.2))
    mp = mask.load()

    # 2) Texture-preserving edit: adjust channels in place; keep high-frequency detail.
    result = image.copy()
    rp = result.load()
    whiten = float(profile.get("whiten", 0.5))
    clean = float(profile.get("clean", 0.65))
    brighten = float(profile.get("brighten", 0.12))

    for y in range(height):
        for x in range(width):
            a = mp[x, y]
            if a < 12:
                continue
            strength = max(0.45, a / 255.0)
            r, g, b = src[x, y]
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            rg = (r + g) * 0.5

            # Skip residual cavity pixels that leaked into soft mask.
            if luma < 52 and (max(r, g, b) - min(r, g, b)) < 28:
                continue

            yellow = max(0.0, (rg - b) / 80.0)
            brown = max(0.0, (r - b) / 55.0)
            darkness = max(0.0, (165 - luma) / 165.0)

            # A) Clean deposits: lift dark/brown while keeping microtexture.
            deposit = max(brown * 0.85, darkness * 0.7 if brown > 0.1 else darkness * 0.35)
            if deposit > 0.04 and clean > 0:
                c = min(0.88, clean * (0.45 + deposit)) * strength
                target_l = min(215.0, luma + 70.0 * c)
                scale = target_l / max(luma, 1.0)
                nr = r * scale
                ng = g * scale
                nb = b * scale + (rg - b) * 0.7 * c
                # Cool the brown cast
                if r > g:
                    nr = nr - (r - g) * 0.4 * c
                keep = 0.18
                r = _clamp(nr * (1 - keep) + r * keep)
                g = _clamp(ng * (1 - keep) + g * keep)
                b = _clamp(nb * (1 - keep) + b * keep)
                luma = 0.299 * r + 0.587 * g + 0.114 * b
                rg = (r + g) * 0.5

            # B) Whiten yellow: push blue up + cool shift, preserve grain.
            if whiten > 0:
                yellow = max(0.0, (rg - b) / 80.0)
                w = min(0.82, whiten * (0.4 + yellow * 0.9)) * strength
                b = _clamp(b + (rg - b) * 0.85 * w + 8 * w)
                if r > g:
                    r = _clamp(r - (r - g) * 0.45 * w)
                lift = 1.0 + brighten * (0.6 + w)
                r = _clamp(r * lift)
                g = _clamp(g * lift)
                b = _clamp(b * lift)

            # Hard cap: never blow out to plastic white.
            if max(r, g, b) > 242:
                over = max(r, g, b) - 242
                r = _clamp(r - over * 0.75)
                g = _clamp(g - over * 0.75)
                b = _clamp(b - over * 0.55)

            rp[x, y] = (r, g, b)

    return result


def _hybrid_local_tooth_simulation(
    image_bytes: bytes,
    report_text: Optional[str] = None,
    findings_json: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> str:
    """
    Identity-preserving simulation on the uploaded pixels.
    Full resolution, no banner, no generative replacement.
    """
    from PIL import Image

    profile = _edit_profile(findings_json, report_text)
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Keep full resolution for sharpness. Only cap extremely large uploads.
    max_edge = 2400
    w, h = image.size
    if max(w, h) > max_edge:
        scale = max_edge / float(max(w, h))
        image = image.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )

    result = _tooth_mask_and_edit(image, profile)
    out = io.BytesIO()
    result.save(out, format="JPEG", quality=95, optimize=True, subsampling=0)
    b64 = base64.b64encode(out.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def _local_smile_simulation(image_bytes: bytes) -> str:
    """Backward-compatible alias → hybrid local tooth edit. """
    return _hybrid_local_tooth_simulation(image_bytes)


def _prepare_image_for_edit(image_bytes: bytes, mime_type: Optional[str] = None) -> tuple:
    """Downscale large uploads for optional Gemini path."""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        max_edge = 1280
        w, h = image.size
        if max(w, h) > max_edge:
            scale = max_edge / float(max(w, h))
            image = image.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=90, optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return image_bytes, (mime_type or "image/jpeg")


def _assessment_blob(report_text: Optional[str], findings_json: Optional[str]) -> str:
    parts = []
    findings = _parse_findings(findings_json)
    if isinstance(findings, dict):
        for concern in findings.get("visible_concerns") or []:
            parts.append(str(concern))
        for sign in findings.get("observed_signs") or []:
            if isinstance(sign, dict):
                parts.extend(
                    [
                        str(sign.get("sign", "")),
                        str(sign.get("concern_label", "")),
                        str(sign.get("visible_feature", "")),
                        str(sign.get("location", "")),
                        str(sign.get("evidence", "")),
                    ]
                )
            else:
                parts.append(str(sign))
        for detail in findings.get("concern_details") or []:
            if isinstance(detail, dict):
                parts.append(str(detail.get("concern", "")))
                parts.append(str(detail.get("description", "")))
    if report_text:
        parts.append(report_text)
    return " ".join(parts).lower()


def _has_missing_teeth(report_text: Optional[str], findings_json: Optional[str]) -> bool:
    """True when the assessment indicates missing teeth / large edentulous gaps."""
    blob = _assessment_blob(report_text, findings_json)
    if not blob.strip():
        return False
    tokens = (
        "missing tooth",
        "missing teeth",
        "tooth missing",
        "teeth missing",
        "absent tooth",
        "absent teeth",
        "edentulous",
        "partially edentulous",
        "no front teeth",
        "lost tooth",
        "lost teeth",
        "extracted",
        "extraction site",
        "tooth loss",
        "space where tooth",
        "gap where tooth",
        "missing anterior",
        "missing incisor",
        "missing central",
    )
    if any(t in blob for t in tokens):
        return True
    # Compact concern labels like "missing_teeth"
    compact = blob.replace(" ", "_")
    return "missing_tooth" in compact or "missing_teeth" in compact


def _map_concern_to_edit(
    concern: str,
    options: list,
    location: Optional[str] = None,
    visible_feature: Optional[str] = None,
) -> str:
    """Map a report concern + suggested treatments to a photo-edit instruction."""
    key = str(concern or "").lower().replace(" ", "_")
    option_text = ", ".join(str(o) for o in (options or [])[:3] if str(o).strip())
    where = str(location or "the affected region visible in the photo").strip()
    seen = str(visible_feature or "").strip()
    seen_bit = f' Visible issue: "{seen}".' if seen else ""
    treat_bit = f" Report treatments to show: {option_text}." if option_text else ""

    if any(token in key for token in ("stain", "color", "colour", "discolor", "whiten")):
        return (
            f"- Staining / colour at {where}:{seen_bit}{treat_bit} "
            "REQUIRED FULL ARCH: remove brown/yellow stains and gently whiten EVERY visible tooth "
            "(left, center, right, upper, lower — including gap-fill teeth) to one even natural cleaned shade. "
            "Do not leave side teeth dirty while fronts are white. "
            "Do not create new black/brown specks, spots, or decay marks on any tooth. "
            "Not pure-white Hollywood veneers."
        )
    if any(token in key for token in ("crowd", "align", "malalign", "rotation", "spacing", "overlap", "ortho")):
        return (
            f"- Alignment at {where}:{seen_bit}{treat_bit} "
            "Apply a subtle after-treatment alignment on those same teeth in this photo. "
            "Keep lips, gums, lighting, and overall mouth identity unchanged."
        )
    if any(token in key for token in ("gum", "gingiv", "plaque", "calculus", "tartar", "deposit", "hygiene")):
        return (
            f"- Plaque / tartar / gum deposits at {where}:{seen_bit}{treat_bit} "
            "REQUIRED FULL ARCH: strip ALL visible plaque, tartar, calculus, and dirty deposits from "
            "EVERY tooth in the photo — left side, center, right side, upper and lower "
            "(including any newly filled gap teeth). No brown rings left on side teeth. "
            "Never add new dark spots while cleaning."
        )
    if any(token in key for token in ("chip", "fracture", "worn", "wear", "crack")):
        return (
            f"- Chip / wear at {where}:{seen_bit}{treat_bit} "
            "Restore only that damaged edge on the same tooth so it looks treated, "
            "then match the cleaned arch shade."
        )
    if any(token in key for token in ("restoration", "crown", "veneer", "filling")):
        return (
            f"- Restorations at {where}:{seen_bit}{treat_bit} "
            "Blend or refresh existing restorations so colour matches the cleaned neighbouring teeth. "
            "Do not replace the whole smile."
        )
    if any(token in key for token in ("missing", "edentulous", "absent_tooth", "tooth_loss", "implant", "bridge", "denture")):
        return (
            f"- Missing teeth / gaps at {where}:{seen_bit}{treat_bit} "
            "Fill empty gaps with teeth that match this person's SHAPE, size, and proportions. "
            "Do NOT copy the dirty/plaque colour into the gap. "
            "After filling, those new teeth must also be cleaned and whitened with the rest of the arch."
        )

    label = str(concern).replace("_", " ")
    if option_text:
        return (
            f"- {label} at {where}:{seen_bit} "
            f"Visually apply these report treatments on this same photo: {option_text}. "
            "Match the person's existing tooth style and keep lips/framing identical."
        )
    return (
        f"- {label} at {where}:{seen_bit} "
        "Apply a realistic treated look on the same teeth in this photo only."
    )


def _treatment_edit_brief(report_text: Optional[str], findings_json: Optional[str]) -> str:
    """Build a treatment-driven edit brief from assessment findings + report."""
    edits = []
    plan_lines = []
    roadmap_lines = []

    findings = _parse_findings(findings_json)

    if isinstance(findings, dict):
        details = findings.get("concern_details") or []
        detail_map = {
            d.get("concern"): d
            for d in details
            if isinstance(d, dict) and d.get("concern")
        }

        signs = findings.get("observed_signs") or []
        for sign in signs:
            if not isinstance(sign, dict):
                continue
            concern = (
                sign.get("concern_label")
                or sign.get("sign")
                or sign.get("concern")
                or "dental_issue"
            )
            detail = detail_map.get(concern) or {}
            options = detail.get("treatment_options") or []
            edits.append(
                _map_concern_to_edit(
                    str(concern),
                    options,
                    location=sign.get("location"),
                    visible_feature=sign.get("visible_feature") or sign.get("evidence"),
                )
            )

        for concern in findings.get("visible_concerns") or []:
            detail = detail_map.get(concern) or {}
            options = detail.get("treatment_options") or []
            cause = detail.get("likely_cause")
            label = str(concern).replace("_", " ")
            if options:
                plan_lines.append(f"- {label}: {', '.join(str(o) for o in options[:4])}")
            else:
                plan_lines.append(f"- {label}")
            if cause:
                plan_lines.append(f"  cause noted: {cause}")
            # Ensure each concern has an edit even if no observed_sign matched
            edits.append(_map_concern_to_edit(str(concern), options))

        for step in findings.get("treatment_roadmap") or []:
            roadmap_lines.append(f"- {step}")

    if not edits and report_text:
        lowered = report_text.lower()
        if "stain" in lowered or "colour" in lowered or "color" in lowered or "whiten" in lowered:
            edits.append(_map_concern_to_edit("staining", ["professional cleaning", "whitening"]))
        if "crowd" in lowered or "align" in lowered or "ortho" in lowered:
            edits.append(_map_concern_to_edit("alignment", ["aligners or orthodontics"]))
        if "gum" in lowered or "plaque" in lowered or "calculus" in lowered or "tartar" in lowered:
            edits.append(_map_concern_to_edit("plaque_tartar", ["professional cleaning / scaling"]))
        if "chip" in lowered or "worn" in lowered:
            edits.append(_map_concern_to_edit("chipped_tooth", ["bonding or restoration"]))
        if "missing" in lowered or "edentulous" in lowered or "implant" in lowered or "bridge" in lowered:
            edits.append(
                _map_concern_to_edit(
                    "missing_teeth",
                    ["implant or bridge replacement matching existing teeth"],
                )
            )

    if not edits:
        edits = [
            "- Professional cleaning look: remove plaque/stains on existing tooth surfaces only.",
            "- Keep the same person, lips, framing, and lighting.",
        ]

    unique = []
    for item in edits:
        if item not in unique:
            unique.append(item)

    plan_block = ""
    if plan_lines:
        plan_block = (
            "TREATMENT PLAN FROM THE ASSESSMENT REPORT (apply these visually):\n"
            + "\n".join(plan_lines[:16])
            + "\n\n"
        )
    if roadmap_lines:
        plan_block += (
            "SUGGESTED ROADMAP (reflect the end result of these treatments):\n"
            + "\n".join(roadmap_lines[:8])
            + "\n\n"
        )
    if report_text and not plan_lines:
        # Fallback: pass a trimmed report excerpt so Qwen still sees treatments
        plan_block = (
            "ASSESSMENT REPORT EXCERPT (apply the treatments described):\n"
            f"{report_text[:3500]}\n\n"
        )

    identity = (
        "REQUIRED EDIT SEQUENCE (do all steps on this same uploaded photo):\n"
        "1) GAP FILL (if missing teeth): add replacement teeth matching this person's "
        "SHAPE, size, and proportions only — do NOT copy plaque/brown stains into the gap.\n"
        "2) FULL-ARCH CLEAN: remove ALL plaque, tartar, calculus, and brown/yellow deposits "
        "from EVERY visible tooth — left side, center, AND right side; upper AND lower; "
        "fronts AND back teeth; original teeth AND gap-fill teeth. No tooth may keep heavy stains.\n"
        "3) FULL-ARCH WHITEN: bring EVERY visible tooth to ONE even, natural cleaned shade "
        "(healthy enamel, not Hollywood veneer white). Left, center, and right must match.\n"
        "4) Keep the same person, lips, skin, camera framing, lighting, and background.\n"
        "5) No text, banners, watermarks, or labels.\n"
        "REJECT THESE FAILURE MODES:\n"
        "- Bright clean fronts next to still-brown side teeth\n"
        "- Only cleaning the middle while molars/canines stay dirty\n"
        "- Filling gaps with dirty/stained clone teeth\n"
        "- Inventing new black spots / dark decay marks on previously clean enamel"
    )
    allowed = "TREATMENT-SPECIFIC EDITS:\n" + "\n".join(unique[:12])
    return f"{plan_block}{identity}\n\n{allowed}"


def _wavespeed_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _wavespeed_request_json(url: str, api_key: str, payload: Optional[dict] = None) -> dict:
    data = None
    method = "GET"
    if payload is not None:
        method = "POST"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=_wavespeed_headers(api_key),
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        detail = err_body[:500] if err_body else str(e)
        raise RuntimeError(f"WaveSpeed HTTP {e.code}: {detail}") from e
    parsed = json.loads(body) if body else {}
    if isinstance(parsed, dict) and parsed.get("code") not in (None, 200) and not parsed.get("data"):
        raise RuntimeError(f"WaveSpeed error: {parsed.get('message') or parsed}")
    if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
        return parsed["data"]
    return parsed if isinstance(parsed, dict) else {}


def _output_to_data_url(output, default_mime: str = "image/jpeg") -> Optional[str]:
    if not output:
        return None
    if isinstance(output, dict):
        output = (
            output.get("url")
            or output.get("image")
            or output.get("b64_json")
            or output.get("base64")
        )
    if not isinstance(output, str) or not output.strip():
        return None
    value = output.strip()
    if value.startswith("data:image/"):
        return value
    if value.startswith("http://") or value.startswith("https://"):
        req = urllib.request.Request(value, method="GET")
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            mime = resp.headers.get("Content-Type") or default_mime
        if mime.startswith("image/"):
            b64 = base64.b64encode(raw).decode("utf-8")
            return f"data:{mime};base64,{b64}"
        return None
    # Naked base64 from enable_base64_output=true
    try:
        raw = base64.b64decode(value, validate=False)
        if len(raw) < 100:
            return None
        b64 = base64.b64encode(raw).decode("utf-8")
        return f"data:{default_mime};base64,{b64}"
    except Exception:
        return None


def _qwen_smile_simulation(
    image_bytes: bytes,
    report_text: Optional[str] = None,
    findings_json: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> Optional[str]:
    """WaveSpeed Qwen-Image-Edit-2511 path. Returns data URL or None on failure."""
    api_key = (os.getenv("WAVESPEED_API_KEY") or "").strip()
    if not api_key:
        return None

    edit_bytes, edit_mime = _prepare_image_for_edit(image_bytes, mime_type)
    treatment_brief = _treatment_edit_brief(report_text, findings_json)
    data_uri = f"data:{edit_mime};base64,{base64.b64encode(edit_bytes).decode('utf-8')}"

    prompt = f"""You are editing the patient's UPLOADED dental photo in place.

Apply the assessment treatments as a finished treated result on THIS same photograph.
Do NOT generate a new person or stock Hollywood smile.

CRITICAL SUCCESS CRITERIA — the after photo must show ONE even cleaned shade on ALL visible teeth:
- Left side teeth cleaned + whitened
- Center teeth cleaned + whitened
- Right side teeth cleaned + whitened
- Upper AND lower arches
- Including any gap-fill teeth
If any side/back tooth is still brown/plaque-covered while fronts are white, the edit FAILED — redo mentally and clean those too.
If the edit introduces new black spots, dark specks, or new decay-like marks, the edit FAILED — remove them.

Must do ALL of these:
1. If there are gaps/missing teeth: fill them with teeth matching this person's shape/size/proportions (not dirty colour clones).
2. Remove plaque, tartar, and brown deposits from EVERY visible tooth (not just the front four).
3. Gently whiten EVERY visible tooth to the SAME natural cleaned shade.
4. Keep the same person, lips, framing, lighting, and background.
5. No text, banners, watermarks, or labels.
6. Do NOT invent new black spots, dark dots, or new decay marks on any tooth.

{treatment_brief}

OUTPUT: one edited version of the uploaded photo with full-arch clean + even whitening."""

    endpoint = (
        os.getenv("WAVESPEED_EDIT_ENDPOINT")
        or "https://api.wavespeed.ai/api/v3/wavespeed-ai/qwen-image/edit-2511"
    ).strip()

    payload = {
        "prompt": prompt,
        "images": [data_uri],
        "seed": -1,
        "output_format": "jpeg",
        "enable_base64_output": True,
        "enable_sync_mode": True,
    }

    try:
        task = _wavespeed_request_json(endpoint, api_key, payload)
    except Exception:
        return None

    status = str(task.get("status") or "").lower()
    outputs = task.get("outputs") or []
    if status == "completed" and outputs:
        return _output_to_data_url(outputs[0])

    prediction_id = task.get("id")
    result_url = None
    urls = task.get("urls") if isinstance(task.get("urls"), dict) else {}
    if urls:
        result_url = urls.get("get")
    if not result_url and prediction_id:
        result_url = f"https://api.wavespeed.ai/api/v3/predictions/{prediction_id}/result"
    if not result_url:
        return None

    for _ in range(45):
        time.sleep(2)
        try:
            result = _wavespeed_request_json(result_url, api_key)
        except Exception:
            continue
        status = str(result.get("status") or "").lower()
        if status == "completed":
            outs = result.get("outputs") or []
            if outs:
                return _output_to_data_url(outs[0])
            return None
        if status in ("failed", "cancelled", "timeout"):
            return None
    return None


def _gemini_smile_simulation(
    image_bytes: bytes,
    report_text: Optional[str] = None,
    findings_json: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> Optional[str]:
    """Gemini image-edit path. Returns data URL or None on failure."""
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    model = os.getenv("GEMINI_SIMULATION_MODEL", "gemini-3.1-flash-image")
    edit_bytes, edit_mime = _prepare_image_for_edit(image_bytes, mime_type)
    treatment_brief = _treatment_edit_brief(report_text, findings_json)

    prompt = f"""You are a dental photo EDITOR editing the attached uploaded photo in place.

Apply the assessment treatments as a finished treated result on THIS same photograph.
Do NOT generate a new person or Hollywood stock smile.

CRITICAL: clean and whiten EVERY visible tooth to ONE even natural shade —
left, center, and right; upper and lower. Bright fronts next to dirty sides = FAILED edit.
If new black spots/dots/decay marks appear, the edit FAILED.

Must do ALL of these:
1. Fill missing-tooth gaps with teeth matching this person's shape/size/proportions (not dirty colour clones).
2. Remove plaque/tartar/brown deposits from EVERY visible tooth, including sides and back teeth.
3. Gently whiten EVERY visible tooth to the same natural cleaned shade (not veneer-white).
4. Keep the same person, lips, gums framing, lighting, and background.
5. No text, banners, watermarks, or labels.
6. Do NOT invent new black spots, dark dots, or new decay marks on any tooth.

{treatment_brief}

OUTPUT: one edited version of THIS uploaded photo with full-arch clean + even whitening.
"""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=[
                prompt,
                types.Part.from_bytes(data=edit_bytes, mime_type=edit_mime),
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        parts = list(getattr(response, "parts", []) or [])
        if not parts and getattr(response, "candidates", None):
            parts = list(getattr(response.candidates[0].content, "parts", []) or [])

        for part in parts:
            if getattr(part, "inline_data", None) is None:
                continue
            try:
                image = part.as_image()
                out = io.BytesIO()
                image.convert("RGB").save(out, format="JPEG", quality=95, optimize=True, subsampling=0)
                b64 = base64.b64encode(out.getvalue()).decode("utf-8")
                return f"data:image/jpeg;base64,{b64}"
            except Exception:
                inline = part.inline_data
                raw = getattr(inline, "data", None)
                mime = getattr(inline, "mime_type", "image/png")
                if isinstance(raw, str):
                    raw = base64.b64decode(raw)
                if isinstance(raw, (bytes, bytearray)):
                    b64 = base64.b64encode(bytes(raw)).decode("utf-8")
                    return f"data:{mime};base64,{b64}"
    except Exception:
        return None
    return None


def create_smile_simulation(
    image_bytes: bytes,
    report_text: Optional[str] = None,
    findings_json: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> str:
    """
    Illustrative after-photo simulation.

    Default: Qwen-Image-Edit-2511 via WaveSpeed — edit the uploaded photo in place
    using treatments from the assessment report (including gap fills that match
    existing teeth, plaque removal, whitening, etc.).
    Falls back to Gemini, then local whitening/cleaning.
    Set SIMULATION_ENGINE=local|gemini|qwen to force a path.
    """
    engine = (os.getenv("SIMULATION_ENGINE") or "qwen").strip().lower()
    kwargs = dict(
        report_text=report_text,
        findings_json=findings_json,
        mime_type=mime_type,
    )

    if engine in ("local", "hybrid", "opencv", "pixel"):
        return _hybrid_local_tooth_simulation(image_bytes, **kwargs)

    if engine in ("gemini", "ai", "generative"):
        ai = _gemini_smile_simulation(image_bytes, **kwargs)
        if ai:
            return ai
        return _hybrid_local_tooth_simulation(image_bytes, **kwargs)

    # Default / qwen / wavespeed — always send report treatments to Qwen
    ai = _qwen_smile_simulation(image_bytes, **kwargs)
    if ai:
        return ai

    if (os.getenv("GEMINI_API_KEY") or "").strip():
        ai = _gemini_smile_simulation(image_bytes, **kwargs)
        if ai:
            return ai

    return _hybrid_local_tooth_simulation(image_bytes, **kwargs)

"""
Patient-facing helpers: report chat and illustrative smile simulation.
"""

import base64
import io
import os
from typing import Optional


def build_chat_prompt(question: str, report_text: str, overall_score: Optional[int]) -> str:
    score_line = f"Overall Smile Score: {overall_score}/100\n" if overall_score is not None else ""
    return f"""You are a friendly dental clinic assistant for The Global Dentist.
Answer the patient's question using ONLY the assessment report below.
Use simple plain English. Do not invent new findings.
Always remind them this is a preliminary AI assessment, not a diagnosis, when giving treatment advice.
Keep answers concise (3-6 short sentences).

{score_line}
Assessment report:
{report_text[:6000]}

Patient question: {question}
"""


def _local_smile_simulation(image_bytes: bytes) -> str:
    """
    Create an illustrative whitened/brightened preview of the smile photo.
    This is NOT an AI clinical result - a visual simulation for engagement.
    Returns a JPEG data URL.
    """
    from PIL import Image, ImageEnhance, ImageDraw, ImageFont

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Keep simulation fast and consistent.
    max_edge = 900
    w, h = image.size
    if max(w, h) > max_edge:
        scale = max_edge / float(max(w, h))
        image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)

    # Mild brighten + slight desaturation of yellow tones for a "whitening" feel.
    image = ImageEnhance.Brightness(image).enhance(1.08)
    image = ImageEnhance.Color(image).enhance(0.88)
    image = ImageEnhance.Contrast(image).enhance(1.05)

    # Soften yellows by blending toward cooler whites on midtones.
    pixels = image.load()
    width, height = image.size
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            r, g, b = pixels[x, y]
            if r > 140 and g > 120 and b > 90 and abs(r - g) < 55:
                nr = min(255, int(r * 0.92 + 255 * 0.08))
                ng = min(255, int(g * 0.94 + 255 * 0.06))
                nb = min(255, int(b * 0.98 + 255 * 0.08))
                pixels[x, y] = (nr, ng, nb)
                if x + 1 < width:
                    pixels[x + 1, y] = (nr, ng, nb)
                if y + 1 < height:
                    pixels[x, y + 1] = (nr, ng, nb)
                if x + 1 < width and y + 1 < height:
                    pixels[x + 1, y + 1] = (nr, ng, nb)

    draw = ImageDraw.Draw(image)
    label = "SIMULATION - NOT A GUARANTEED RESULT"
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    draw.rectangle((8, height - 28, width - 8, height - 8), fill=(6, 40, 72, 180))
    draw.text((14, height - 24), label, fill=(255, 255, 255), font=font)

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85, optimize=True)
    b64 = base64.b64encode(out.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def create_smile_simulation(image_bytes: bytes, report_text: Optional[str] = None) -> str:
    """
    Generate an illustrative "after" smile image using Gemini image generation.
    Falls back to local simulation if the API is unavailable.
    """
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _local_smile_simulation(image_bytes)

    model = os.getenv("GEMINI_SIMULATION_MODEL", "gemini-2.5-flash-image")
    report_hint = (report_text or "")[:900]
    prompt = (
        "Edit this exact patient's smile photo into a realistic, natural-looking AFTER simulation. "
        "Keep the same person identity, pose, framing, skin tone, and lighting as much as possible. "
        "Apply subtle cosmetic improvements like cleaner shade, smoother alignment, and refined tooth shape. "
        "Do not make exaggerated, fake, or plastic-looking teeth. "
        "Do not add braces, masks, text labels, watermarks, or extra people."
    )
    if report_hint:
        prompt += (
            " Optional treatment context from report (use lightly, do not overfit): "
            f"{report_hint}"
        )

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=[
                prompt,
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
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
                image.convert("RGB").save(out, format="JPEG", quality=90, optimize=True)
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
        pass

    return _local_smile_simulation(image_bytes)

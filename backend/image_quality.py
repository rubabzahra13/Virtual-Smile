"""
Image quality checks before calling a vision model.

Local heuristics reject obviously broken uploads without an API call.
Gemini flash-lite handles semantic quality when provider is gemini.
"""

import io
import json
import struct
from typing import List, Optional, Tuple

from scoring import ScoringError, _extract_json_block

MIN_IMAGE_BYTES = 15_000
MIN_WIDTH = 400
MIN_HEIGHT = 300
MAX_ASPECT_RATIO = 3.0

# Keep Groq free-tier vision requests under ~8k TPM by shrinking large uploads.
GROQ_MAX_LONG_EDGE = 896
GROQ_JPEG_QUALITY = 72
GROQ_MAX_BYTES = 350_000


def _jpeg_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    """Read width/height from JPEG headers without Pillow."""
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        return None
    index = 2
    while index < len(data) - 8:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height = struct.unpack(">H", data[index + 5 : index + 7])[0]
            width = struct.unpack(">H", data[index + 7 : index + 9])[0]
            return width, height
        if marker in (0xD8, 0xD9):
            break
        length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        index += 2 + length
    return None


def _png_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    if len(data) < 24 or data[0:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width = struct.unpack(">I", data[16:20])[0]
    height = struct.unpack(">I", data[20:24])[0]
    return width, height


def _image_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    dims = _jpeg_dimensions(data)
    if dims:
        return dims
    return _png_dimensions(data)


def _brightness_score(data: bytes) -> Optional[float]:
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(data)).convert("L")
        pixels = list(image.getdata())
        if not pixels:
            return None
        return sum(pixels) / len(pixels) / 255.0
    except Exception:
        return None


def check_image_quality(images: List[Tuple[bytes, str]], labels: List[str]) -> dict:
    """
    Returns:
    {
        "ok": bool,
        "issues": [str],
        "per_image": [{label, ok, issues, width, height, brightness}],
    }
    """
    per_image = []
    all_issues = []

    for label, (image_bytes, _mime) in zip(labels, images):
        issues = []
        width = None
        height = None
        brightness = None

        if len(image_bytes) < MIN_IMAGE_BYTES:
            issues.append(
                f"{label}: image file is very small ({len(image_bytes)} bytes) - likely too low resolution"
            )

        dims = _image_dimensions(image_bytes)
        if dims:
            width, height = dims
            if width < MIN_WIDTH or height < MIN_HEIGHT:
                issues.append(
                    f"{label}: resolution too low ({width}x{height}); need at least {MIN_WIDTH}x{MIN_HEIGHT}"
                )
            aspect = max(width, height) / max(min(width, height), 1)
            if aspect > MAX_ASPECT_RATIO:
                issues.append(f"{label}: unusual aspect ratio ({width}x{height})")
        else:
            issues.append(f"{label}: could not read image dimensions - file may be corrupt")

        brightness = _brightness_score(image_bytes)
        if brightness is not None:
            if brightness < 0.12:
                issues.append(f"{label}: image appears too dark")
            elif brightness > 0.92:
                issues.append(f"{label}: image appears overexposed or washed out")

        per_image.append(
            {
                "label": label,
                "ok": not issues,
                "issues": issues,
                "width": width,
                "height": height,
                "brightness": round(brightness, 3) if brightness is not None else None,
            }
        )
        all_issues.extend(issues)

    return {
        "ok": not all_issues,
        "issues": all_issues,
        "per_image": per_image,
    }


def parse_quality_response(raw_text: str, labels: List[str]) -> dict:
    """Parse Gemini quality gate JSON into the same shape as check_image_quality."""
    try:
        findings = json.loads(_extract_json_block(raw_text))
    except (ScoringError, json.JSONDecodeError, TypeError) as exc:
        return {
            "ok": False,
            "issues": [f"Quality check response could not be parsed: {exc}"],
            "per_image": [{"label": label, "ok": False, "issues": ["parse error"]} for label in labels],
            "parse_error": str(exc),
        }

    ok = bool(findings.get("ok", False))
    issues = findings.get("issues") or []
    if not isinstance(issues, list):
        issues = [str(issues)]

    per_image = []
    raw_per_image = findings.get("per_image") or []
    if isinstance(raw_per_image, list):
        for entry in raw_per_image:
            if not isinstance(entry, dict):
                continue
            label = entry.get("label") or "unknown"
            entry_issues = entry.get("issues") or []
            if not isinstance(entry_issues, list):
                entry_issues = [str(entry_issues)]
            per_image.append(
                {
                    "label": label,
                    "ok": bool(entry.get("ok", not entry_issues)),
                    "issues": entry_issues,
                    "width": entry.get("width"),
                    "height": entry.get("height"),
                    "brightness": entry.get("brightness"),
                }
            )

    if not per_image:
        per_image = [{"label": label, "ok": ok, "issues": issues} for label in labels]

    if not ok and not issues:
        issues = [
            issue
            for entry in per_image
            for issue in (entry.get("issues") or [])
        ]

    return {"ok": ok and not issues, "issues": issues, "per_image": per_image}


def check_image_quality_with_model(
    provider: str,
    images: List[Tuple[bytes, str]],
    labels: List[str],
    quality_model: str,
) -> Tuple[dict, dict]:
    """
    Run a cheap vision model quality gate. Returns (quality_result, usage_dict).
    Falls back to local heuristics if the model response cannot be parsed.
    """
    from prompts import build_image_quality_prompt
    from providers import call_provider

    local_result = check_image_quality(images, labels)
    if not local_result["ok"]:
        return local_result, None

    prompt = build_image_quality_prompt(labels)
    usage = call_provider(provider, images, prompt, model=quality_model)
    parsed = parse_quality_response(usage["raw_text"], labels)
    if parsed.get("parse_error"):
        parsed = local_result
    return parsed, usage


def resize_image_for_vision(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    max_long_edge: int = GROQ_MAX_LONG_EDGE,
    jpeg_quality: int = GROQ_JPEG_QUALITY,
    max_bytes: int = GROQ_MAX_BYTES,
) -> Tuple[bytes, str]:
    """
    Downscale/compress an image so Groq vision requests stay under free-tier TPM.
    Returns (bytes, mime_type). Falls back to original if Pillow fails.
    """
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")

        width, height = image.size
        long_edge = max(width, height)
        if long_edge > max_long_edge:
            scale = max_long_edge / float(long_edge)
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            image = image.resize(new_size, Image.Resampling.LANCZOS)

        quality = jpeg_quality
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=quality, optimize=True)
        result = out.getvalue()

        # If still too large, step quality down until under max_bytes.
        while len(result) > max_bytes and quality > 45:
            quality -= 8
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=quality, optimize=True)
            result = out.getvalue()

        return result, "image/jpeg"
    except Exception:
        return image_bytes, mime_type or "image/jpeg"


def resize_images_for_vision(
    images: List[Tuple[bytes, str]],
) -> List[Tuple[bytes, str]]:
    return [resize_image_for_vision(data, mime) for data, mime in images]

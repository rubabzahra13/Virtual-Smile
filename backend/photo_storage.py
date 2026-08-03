"""Compress and store assessment smile photos in Supabase Storage."""

from __future__ import annotations

import io
import logging
from typing import Any, Optional

from db import get_supabase

logger = logging.getLogger(__name__)

BUCKET = "assessment-photos"
ADMIN_MAX_LONG_EDGE = 1600
ADMIN_JPEG_QUALITY = 80
SIGNED_URL_SECONDS = 60 * 60  # 1 hour


def compress_for_admin(image_bytes: bytes) -> bytes:
    """Downscale + JPEG compress for space-efficient admin previews."""
    from PIL import Image, ImageOps

    image = Image.open(io.BytesIO(image_bytes))
    try:
        image = ImageOps.exif_transpose(image) or image
    except Exception:
        pass
    if image.mode != "RGB":
        image = image.convert("RGB")

    width, height = image.size
    long_edge = max(width, height)
    if long_edge > ADMIN_MAX_LONG_EDGE:
        scale = ADMIN_MAX_LONG_EDGE / float(long_edge)
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=ADMIN_JPEG_QUALITY, optimize=True)
    return out.getvalue()


def ensure_photo_bucket() -> None:
    sb = get_supabase()
    try:
        buckets = sb.storage.list_buckets()
        names = set()
        for b in buckets or []:
            name = getattr(b, "name", None) or getattr(b, "id", None)
            if name is None and isinstance(b, dict):
                name = b.get("name") or b.get("id")
            if name:
                names.add(name)
        if BUCKET in names:
            return
    except Exception:
        logger.exception("Could not list storage buckets")

    try:
        sb.storage.create_bucket(
            BUCKET,
            options={
                "public": False,
                "file_size_limit": 2_097_152,
                "allowed_mime_types": ["image/jpeg"],
            },
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "already exists" not in msg and "duplicate" not in msg:
            logger.exception("Could not create storage bucket %s", BUCKET)


def upload_assessment_photos(
    assessment_id: str,
    images: Optional[list],
) -> dict[str, str]:
    """
    Upload compressed Front/Left/Right views.
    `images` is list of (label, bytes) as passed from analyze.
    Returns {photo_front_path, ...} for non-empty uploads.
    """
    if not assessment_id or not images:
        return {}

    labeled: list[tuple[str, bytes]] = []
    for item in images:
        if not isinstance(item, (tuple, list)) or len(item) < 2:
            continue
        label = str(item[0] or "").strip().lower()
        raw = item[1]
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            continue
        labeled.append((label, bytes(raw)))

    if not labeled:
        return {}

    ensure_photo_bucket()
    sb = get_supabase()
    paths: dict[str, str] = {}

    def _slot_for(label: str) -> Optional[str]:
        if "front" in label:
            return "front"
        if "left" in label:
            return "left"
        if "right" in label:
            return "right"
        return None

    for label, raw in labeled:
        slot = _slot_for(label)
        if not slot:
            continue
        try:
            compressed = compress_for_admin(raw)
            path = f"{assessment_id}/{slot}.jpg"
            sb.storage.from_(BUCKET).upload(
                path,
                compressed,
                file_options={"content-type": "image/jpeg", "upsert": "true"},
            )
            paths[f"photo_{slot}_path"] = path
        except Exception:
            logger.exception(
                "Failed to upload %s photo for assessment %s", slot, assessment_id
            )

    return paths


def signed_url_for_path(path: str) -> Optional[str]:
    """Create a signed URL for a single storage object path."""
    clean = str(path or "").strip()
    if not clean:
        return None
    try:
        sb = get_supabase()
        res = sb.storage.from_(BUCKET).create_signed_url(clean, SIGNED_URL_SECONDS)
        if isinstance(res, dict):
            return (
                res.get("signedURL")
                or res.get("signedUrl")
                or res.get("signed_url")
            )
        return getattr(res, "signed_url", None) or getattr(res, "signedURL", None)
    except Exception:
        logger.exception("Could not sign photo URL for %s", clean)
        return None


def signed_photo_urls(report: dict[str, Any]) -> dict[str, Optional[str]]:
    """Return signed URLs for stored assessment photo paths."""
    out: dict[str, Optional[str]] = {
        "front": None,
        "left": None,
        "right": None,
    }
    for key, col in (
        ("front", "photo_front_path"),
        ("left", "photo_left_path"),
        ("right", "photo_right_path"),
    ):
        out[key] = signed_url_for_path(str(report.get(col) or ""))
    return out


def download_assessment_photo_bytes(path: str) -> Optional[bytes]:
    """Download photo raw bytes from Supabase Storage by object path."""
    clean = str(path or "").strip()
    if not clean:
        return None
    try:
        sb = get_supabase()
        return sb.storage.from_(BUCKET).download(clean)
    except Exception:
        logger.exception("Could not download assessment photo bytes for %s", clean)
        return None

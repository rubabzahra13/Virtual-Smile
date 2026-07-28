"""
main.py

FastAPI backend for The Global Dentist Virtual Smile Assessment.

Endpoints:
  GET  / - branded frontend
  GET  /models - available providers/models
  POST /analyze - multi-provider analysis pipeline
  POST /analyze/compare - Groq comparison pipeline
  POST /chat - report Q&A assistant
  POST /simulate - illustrative before/after preview
"""

import os
import hashlib
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from analysis import run_analysis
from groq_comparison import run_groq_comparison
from patient_features import build_chat_prompt, create_smile_simulation
from providers import call_gemini_text
from report import build_groq_comparison_report, build_report

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="The Global Dentist - Virtual Smile Assessment")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

MODEL_OPTIONS = {
    "gemini": [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.1-flash-image",
    ],
    "claude": ["claude-sonnet-5", "claude-opus-4-8"],
}

DEFAULT_TWO_PASS = os.getenv("TWO_PASS_ENABLED", "true").lower() in ("1", "true", "yes")
DEFAULT_PATIENT_MODEL = os.getenv("PATIENT_CHAT_MODEL", "gemini-3.5-flash-lite")
DEFAULT_ANALYSIS_MODEL = os.getenv("GEMINI_ANALYSIS_MODEL", "gemini-3.5-flash-lite")
DEFAULT_QUALITY_MODEL = os.getenv("GEMINI_QUALITY_MODEL", "gemini-3.5-flash-lite")
ANALYSIS_CACHE_MAX_ITEMS = int(os.getenv("ANALYSIS_CACHE_MAX_ITEMS", "300"))
ANALYSIS_RESULT_CACHE = {}


def _cache_key_for_analysis(
    provider: str,
    model: str,
    quality_model: Optional[str],
    two_pass: bool,
    images: list,
) -> str:
    hasher = hashlib.sha256()
    hasher.update(provider.encode("utf-8"))
    hasher.update(model.encode("utf-8"))
    hasher.update((quality_model or "").encode("utf-8"))
    hasher.update(str(two_pass).encode("utf-8"))
    for image_bytes, mime_type in images:
        hasher.update(mime_type.encode("utf-8"))
        hasher.update(image_bytes)
    return hasher.hexdigest()


def _set_cached_result(cache_key: str, value: dict) -> None:
    if cache_key in ANALYSIS_RESULT_CACHE:
        ANALYSIS_RESULT_CACHE[cache_key] = value
        return
    if len(ANALYSIS_RESULT_CACHE) >= ANALYSIS_CACHE_MAX_ITEMS:
        oldest_key = next(iter(ANALYSIS_RESULT_CACHE))
        ANALYSIS_RESULT_CACHE.pop(oldest_key, None)
    ANALYSIS_RESULT_CACHE[cache_key] = value


def _has_assessment_issues(scoring_result: dict) -> bool:
    findings = scoring_result.get("findings") if isinstance(scoring_result, dict) else None
    if not isinstance(findings, dict):
        return True

    visible_concerns = findings.get("visible_concerns")
    if isinstance(visible_concerns, list) and len(visible_concerns) > 0:
        return True

    observed_signs = findings.get("observed_signs")
    if isinstance(observed_signs, list) and len(observed_signs) > 0:
        return True

    return False


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=2)
    report_text: str = Field(..., min_length=10)
    overall_score: Optional[int] = None
    email: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="frontend/index.html not found")
    return index_path.read_text(encoding="utf-8")


@app.get("/models")
def list_models():
    return {"models": MODEL_OPTIONS, "default_two_pass": DEFAULT_TWO_PASS}


@app.post("/analyze")
async def analyze(
    front_image: UploadFile = File(...),
    left_image: Optional[UploadFile] = File(None),
    right_image: Optional[UploadFile] = File(None),
    provider: str = Form("gemini"),
    model: str = Form(None),
    quality_model: str = Form(None),
    two_pass: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
):
    provider = provider.lower().strip()
    if provider not in MODEL_OPTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{provider}'. Choose from: {list(MODEL_OPTIONS)}",
        )

    default_model = DEFAULT_ANALYSIS_MODEL if provider == "gemini" else MODEL_OPTIONS[provider][0]
    selected_model = model.strip() if model else default_model
    if provider == "gemini":
        # Keep all assessment passes on the same low-cost model.
        selected_model = "gemini-3.5-flash-lite"
    if selected_model not in MODEL_OPTIONS[provider]:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{selected_model}' is not configured for provider '{provider}'. "
            f"Available: {MODEL_OPTIONS[provider]}",
        )

    selected_quality_model = None
    if provider == "gemini":
        selected_quality_model = (
            quality_model.strip() if quality_model else DEFAULT_QUALITY_MODEL
        )
        selected_quality_model = "gemini-3.5-flash-lite"
        if selected_quality_model not in MODEL_OPTIONS[provider]:
            raise HTTPException(
                status_code=400,
                detail=f"Quality model '{selected_quality_model}' is not configured for provider '{provider}'. "
                f"Available: {MODEL_OPTIONS[provider]}",
            )

    use_two_pass = DEFAULT_TWO_PASS
    if two_pass is not None:
        use_two_pass = two_pass.lower() in ("1", "true", "yes")

    front_bytes = await front_image.read()
    if not front_bytes:
        raise HTTPException(status_code=400, detail="Front smile image was empty.")

    images = [(front_bytes, front_image.content_type or "image/jpeg")]

    if left_image is not None:
        left_bytes = await left_image.read()
        if left_bytes:
            images.append((left_bytes, left_image.content_type or "image/jpeg"))

    if right_image is not None:
        right_bytes = await right_image.read()
        if right_bytes:
            images.append((right_bytes, right_image.content_type or "image/jpeg"))

    cache_key = _cache_key_for_analysis(
        provider=provider,
        model=selected_model,
        quality_model=selected_quality_model,
        two_pass=use_two_pass,
        images=images,
    )
    cached_payload = ANALYSIS_RESULT_CACHE.get(cache_key)
    if cached_payload is not None:
        return JSONResponse(cached_payload)

    try:
        result = run_analysis(
            provider=provider,
            images=images,
            model=selected_model,
            two_pass=use_two_pass,
            quality_model=selected_quality_model,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"{provider} API call failed: {type(e).__name__}: {e}",
        )

    if result.get("quality_rejected"):
        quality = result["quality_result"]
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Image quality check failed. Please retake photos with better lighting, focus, and resolution.",
                "issues": quality["issues"],
                "per_image": quality["per_image"],
            },
        )

    scoring_result = result["scoring_result"]
    usage = result["usage"]
    report_text = build_report(
        provider=provider,
        model_name=selected_model,
        scoring_result=scoring_result,
        usage=usage,
        raw_text=result["raw_model_output"],
        images_used=len(images),
        pipeline=result["pipeline"],
        quality_result=result.get("quality_result"),
        detection_result=result.get("detection_result"),
    )

    payload = {
        "provider": provider,
        "model": selected_model,
        "quality_model": selected_quality_model,
        "pipeline": result["pipeline"],
        "two_pass": use_two_pass,
        "images_used": len(images),
        "email": email,
        "phone": phone,
        "report_text": report_text,
        "parsed_ok": scoring_result["parsed_ok"],
        "overall_score": scoring_result["overall_score"],
        "category_scores": scoring_result.get("category_scores"),
        "findings": scoring_result.get("findings"),
        "raw_model_output": result["raw_model_output"],
        "simulation_allowed": _has_assessment_issues(scoring_result),
        "usage": {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
            "latency_seconds": usage["latency_seconds"],
            "passes": usage.get("passes"),
        },
    }
    _set_cached_result(cache_key, json.loads(json.dumps(payload)))
    return JSONResponse(payload)


@app.post("/chat")
async def chat(payload: ChatRequest):
    prompt = build_chat_prompt(
        question=payload.question,
        report_text=payload.report_text,
        overall_score=payload.overall_score,
    )
    try:
        usage = call_gemini_text(prompt, model=DEFAULT_PATIENT_MODEL)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Chat failed: {type(e).__name__}: {e}")

    answer = (usage.get("raw_text") or "").strip()
    if not answer:
        raise HTTPException(status_code=502, detail="Chat model returned an empty answer.")

    return {
        "answer": answer,
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "latency_seconds": usage.get("latency_seconds"),
        },
    }


@app.post("/simulate")
async def simulate(
    front_image: UploadFile = File(...),
    report_text: Optional[str] = Form(None),
):
    front_bytes = await front_image.read()
    if not front_bytes:
        raise HTTPException(status_code=400, detail="Front smile image was empty.")

    try:
        data_url = create_smile_simulation(front_bytes, report_text=report_text)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not create simulation: {type(e).__name__}: {e}",
        )

    return {
        "image_data_url": data_url,
        "disclaimer": (
            "Illustrative simulation only. Not a guaranteed clinical result. "
            "A dentist consultation is required for treatment planning."
        ),
        "report_context_used": bool(report_text),
    }


@app.post("/analyze/compare")
async def analyze_compare(
    front_image: UploadFile = File(...),
    left_image: Optional[UploadFile] = File(None),
    right_image: Optional[UploadFile] = File(None),
):
    front_bytes = await front_image.read()
    if not front_bytes:
        raise HTTPException(status_code=400, detail="Front smile image was empty.")

    images = [(front_bytes, front_image.content_type or "image/jpeg")]

    if left_image is not None:
        left_bytes = await left_image.read()
        if left_bytes:
            images.append((left_bytes, left_image.content_type or "image/jpeg"))

    if right_image is not None:
        right_bytes = await right_image.read()
        if right_bytes:
            images.append((right_bytes, right_image.content_type or "image/jpeg"))

    try:
        result = run_groq_comparison(images)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Groq comparison pipeline failed: {type(e).__name__}: {e}",
        )

    if result.get("quality_rejected"):
        quality = result["quality_result"]
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Image quality check failed. Please retake photos with better lighting, focus, and resolution.",
                "issues": quality["issues"],
                "per_image": quality["per_image"],
            },
        )

    scoring_result = result["scoring_result"]
    usage = result["usage"]
    report_text = build_groq_comparison_report(
        scoring_result=scoring_result,
        usage=usage,
        raw_text=result["raw_model_output"],
        images_used=len(images),
        vision_model=result["vision_model"],
        metrics_model=result["metrics_model"],
        report_model=result["report_model"],
        vision_description=result.get("vision_description", ""),
    )

    return JSONResponse(
        {
            "pipeline": result["pipeline"],
            "vision_model": result["vision_model"],
            "metrics_model": result["metrics_model"],
            "report_model": result["report_model"],
            "images_used": len(images),
            "vision_description": result.get("vision_description"),
            "report_text": report_text,
            "parsed_ok": scoring_result["parsed_ok"],
            "overall_score": scoring_result["overall_score"],
            "raw_model_output": result["raw_model_output"],
            "usage": {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "total_tokens": usage["total_tokens"],
                "latency_seconds": usage["latency_seconds"],
                "passes": usage.get("passes"),
            },
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

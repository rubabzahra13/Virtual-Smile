"""
main.py

Minimal FastAPI backend for the vision-provider comparison POC.

Flow per request:
  1. Receive 1 required front-smile image + up to 2 optional side-profile
     images, plus a provider choice, from the frontend.
  2. Call that provider's vision model with the shared prompt, passing all
     provided images together as one combined request (providers.py).
  3. Parse the model's JSON output and compute a deterministic score
     (scoring.py — this is the "business logic" layer, no AI involved).
  4. Format a plain-text report (report.py).
  5. Return the report text + token usage + latency to the frontend.
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv

from prompts import VISION_ANALYSIS_PROMPT
from providers import call_provider
from scoring import parse_and_score
from report import build_report

load_dotenv()

app = FastAPI(title="Dental Vision Provider Comparison POC")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_OPTIONS = {
    #"openai": ["gpt-5.6-sol"],
    "openai": ["gpt-4.1-nano"],
    "gemini": [
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3.1-pro",
        "gemini-2.5-pro",
        # gemini-2.5-flash removed: Google has deprecated it for new API
        # keys (404 "no longer available to new users"), even though it
        # still showed up in the models.list() discovery output.
    ],
    "groq": ["qwen/qwen3.6-27b"],
    "claude": ["claude-sonnet-5", "claude-opus-4-8"],
}

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="frontend/index.html not found")
    return index_path.read_text(encoding="utf-8")


@app.post("/analyze")
async def analyze(
    front_image: UploadFile = File(...),
    left_image: Optional[UploadFile] = File(None),
    right_image: Optional[UploadFile] = File(None),
    provider: str = Form(...),
    model: str = Form(None),
):
    provider = provider.lower().strip()
    if provider not in MODEL_OPTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{provider}'. Choose from: {list(MODEL_OPTIONS)}",
        )

    selected_model = model.strip() if model else MODEL_OPTIONS[provider][0]
    if selected_model not in MODEL_OPTIONS[provider]:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{selected_model}' is not configured for provider '{provider}'. "
            f"Available: {MODEL_OPTIONS[provider]}",
        )

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
        usage = call_provider(provider, images, VISION_ANALYSIS_PROMPT, model=selected_model)
    except RuntimeError as e:
        # Missing API key for this provider
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Provider API error (auth, rate limit, network, etc.) — surface it,
        # don't crash the whole app.
        raise HTTPException(
            status_code=502,
            detail=f"{provider} API call failed: {type(e).__name__}: {e}",
        )

    scoring_result = parse_and_score(usage["raw_text"])
    report_text = build_report(
        provider=provider,
        model_name=selected_model,
        scoring_result=scoring_result,
        usage=usage,
        raw_text=usage["raw_text"],
        images_used=len(images),
    )

    return JSONResponse(
        {
            "provider": provider,
            "model": selected_model,
            "images_used": len(images),
            "report_text": report_text,
            "parsed_ok": scoring_result["parsed_ok"],
            "overall_score": scoring_result["overall_score"],
            "raw_model_output": usage["raw_text"],
            "usage": {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "total_tokens": usage["total_tokens"],
                "latency_seconds": usage["latency_seconds"],
            },
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
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
import re
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from analysis import run_analysis
from booking_api import admin_router, check_eligibility, persist_assessment, public_router
from leads_api import leads_router
from db import db_ready
from groq_comparison import run_groq_comparison
from language_utils import contains_forbidden_script, detect_chat_language
from patient_features import build_chat_prompt, create_smile_simulation
from providers import call_gemini_text, call_groq_text, call_provider_text
from report import build_groq_comparison_report, build_report
from chat_storage import save_chat_turn, get_chat_history

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="The Global Dentist - Virtual Smile Assessment")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Disable browser caching for frontend assets during local development."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/") or path.startswith("/admin"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


app.add_middleware(NoCacheStaticMiddleware)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

app.include_router(public_router)
app.include_router(admin_router)
app.include_router(leads_router)


def _asset_version(filename: str) -> str:
    path = FRONTEND_DIR / filename
    try:
        return str(int(path.stat().st_mtime))
    except OSError:
        return "1"

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


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=2)
    report_text: str = Field(..., min_length=10)
    overall_score: Optional[int] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    assessment_id: Optional[str] = None
    history: List[ChatMessage] = Field(default_factory=list)


def _frontend_stamp() -> str:
    """Max mtime across frontend source files — used for live reload."""
    latest = 0
    for path in FRONTEND_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".html", ".css", ".js"}:
            continue
        try:
            latest = max(latest, int(path.stat().st_mtime))
        except OSError:
            continue
    return str(latest)


LIVE_RELOAD_SCRIPT = """
<script>
(function () {
  var stamp = %STAMP%;
  function check() {
    fetch("/dev/frontend-version", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (String(data.stamp) !== String(stamp)) {
          location.reload();
        }
      })
      .catch(function () {});
  }
  setInterval(check, 800);
})();
</script>
"""


@app.get("/dev/frontend-version")
def frontend_version():
    return JSONResponse(
        {"stamp": _frontend_stamp()},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/", response_class=HTMLResponse)
def serve_admin():
    admin_path = FRONTEND_DIR / "admin" / "index.html"
    if not admin_path.exists():
        raise HTTPException(status_code=500, detail="frontend/admin/index.html not found")
    html = admin_path.read_text(encoding="utf-8")
    replacements = {
        "/static/admin/admin.css": f"/static/admin/admin.css?v={_asset_version('admin/admin.css')}",
        "/static/admin/admin.js": f"/static/admin/admin.js?v={_asset_version('admin/admin.js')}",
    }
    for old, new in replacements.items():
        html = re.sub(rf"{re.escape(old)}(?:\?v=[^\"']*)?", new, html)
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="frontend/index.html not found")
    html = index_path.read_text(encoding="utf-8")
    # Bust browser cache whenever CSS/JS files change (mtime-based).
    replacements = {
        "/static/styles.css": f"/static/styles.css?v={_asset_version('styles.css')}",
        "/static/mobile.css": f"/static/mobile.css?v={_asset_version('mobile.css')}",
        "/static/app.js": f"/static/app.js?v={_asset_version('app.js')}",
    }
    for old, new in replacements.items():
        html = re.sub(rf"{re.escape(old)}(?:\?v=[^\"']*)?", new, html)

    stamp = _frontend_stamp()
    reload_tag = LIVE_RELOAD_SCRIPT.replace("%STAMP%", json.dumps(stamp))
    if "</body>" in html:
        html = html.replace("</body>", reload_tag + "\n</body>", 1)
    else:
        html += reload_tag

    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


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
    name: Optional[str] = Form(None),
    full_name: Optional[str] = Form(None),
    fullName: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    age: Optional[str] = Form(None),
    city: Optional[str] = Form(None),
):
    provider = provider.lower().strip()
    if provider not in MODEL_OPTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider '{provider}'. Choose from: {list(MODEL_OPTIONS)}",
        )

    patient_name = (full_name or fullName or name or "").strip()
    gender_v = (gender or "").strip()
    city_v = (city or "").strip()
    age_v = None
    if age is not None and str(age).strip():
        try:
            age_v = int(str(age).strip())
        except ValueError:
            pass

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

    if db_ready():
        if not patient_name:
            raise HTTPException(status_code=400, detail="Full Name is required.")
        if not (email or "").strip():
            raise HTTPException(status_code=400, detail="Valid Email Address is required.")
        if not gender_v:
            raise HTTPException(status_code=400, detail="Gender is required.")
        if age_v is None or not (1 <= age_v <= 120):
            raise HTTPException(status_code=400, detail="Age must be a valid number between 1 and 120.")
        if not city_v:
            raise HTTPException(status_code=400, detail="City is required.")
        if not (phone or "").strip():
            raise HTTPException(status_code=400, detail="Mobile phone is required.")

        elig = check_eligibility(email or "", phone or "")
        if not elig.get("ok"):
            raise HTTPException(status_code=409, detail=elig.get("reason") or "You have already taken an assessment.")

    front_bytes = await front_image.read()
    if not front_bytes:
        raise HTTPException(status_code=400, detail="Front smile image was empty.")

    images = [(front_bytes, front_image.content_type or "image/jpeg")]
    report_photos: list[tuple[str, bytes]] = [("Front smile", front_bytes)]

    if left_image is not None:
        left_bytes = await left_image.read()
        if left_bytes:
            images.append((left_bytes, left_image.content_type or "image/jpeg"))
            report_photos.append(("Left smile", left_bytes))

    if right_image is not None:
        right_bytes = await right_image.read()
        if right_bytes:
            images.append((right_bytes, right_image.content_type or "image/jpeg"))
            report_photos.append(("Right smile", right_bytes))

    cache_key = _cache_key_for_analysis(
        provider=provider,
        model=selected_model,
        quality_model=selected_quality_model,
        two_pass=use_two_pass,
        images=images,
    )
    cached_payload = ANALYSIS_RESULT_CACHE.get(cache_key)
    if cached_payload is not None:
        payload = json.loads(json.dumps(cached_payload))
        payload["email"] = email
        payload["phone"] = phone
        payload["name"] = patient_name
        payload["fullName"] = patient_name
        payload["gender"] = gender_v
        payload["age"] = age_v
        payload["city"] = city_v
        try:
            saved = persist_assessment(
                email=email,
                phone=phone,
                name=patient_name,
                gender=gender_v,
                age=age_v,
                city=city_v,
                overall_score=payload.get("overall_score"),
                category_scores=payload.get("category_scores"),
                findings=payload.get("findings"),
                report_text=payload.get("report_text") or "",
                images=report_photos,
            )
            if saved:
                payload["assessment_id"] = saved.get("id")
                payload["email_sent"] = bool(saved.get("email_sent"))
        except HTTPException:
            raise
        except Exception as e:
            payload["persist_error"] = f"{type(e).__name__}: {e}"
        return JSONResponse(payload)

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
        "name": patient_name,
        "fullName": patient_name,
        "email": email,
        "phone": phone,
        "gender": gender_v,
        "age": age_v,
        "city": city_v,
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

    try:
        saved = persist_assessment(
            email=email,
            phone=phone,
            name=patient_name,
            gender=gender_v,
            age=age_v,
            city=city_v,
            overall_score=scoring_result.get("overall_score"),
            category_scores=scoring_result.get("category_scores"),
            findings=scoring_result.get("findings"),
            report_text=report_text,
            images=report_photos,
        )
        if saved:
            payload["assessment_id"] = saved.get("id")
            payload["email_sent"] = bool(saved.get("email_sent"))
    except HTTPException:
        raise
    except Exception as e:
        # Persistence/email failures should not block the patient from seeing results
        # when DB is misconfigured mid-request after eligibility already passed.
        payload["persist_error"] = f"{type(e).__name__}: {e}"

    _set_cached_result(cache_key, json.loads(json.dumps(payload)))
    return JSONResponse(payload)


@app.post("/chat")
async def chat(payload: ChatRequest):
    target_lang = detect_chat_language(payload.question)
    prompt = build_chat_prompt(
        question=payload.question,
        report_text=payload.report_text,
        overall_score=payload.overall_score,
        history=[m.model_dump() for m in payload.history],
        target_lang=target_lang,
    )

    chat_model = DEFAULT_PATIENT_MODEL
    try:
        if "gemini" in chat_model.lower():
            usage = call_gemini_text(prompt, model=chat_model)
        else:
            usage = call_groq_text(prompt, model=chat_model)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Chat failed: {type(e).__name__}: {e}")

    answer = (usage.get("raw_text") or "").strip()

    # Safety check: if forbidden Hindi (Devanagari) or Urdu script is present, retry with strong correction directive
    if contains_forbidden_script(answer):
        correction_prompt = (
            f"CRITICAL OVERRIDE DIRECTIVE:\n"
            f"Your previous response contained forbidden script characters (Hindi Devanagari or Urdu script).\n"
            f"You MUST rewrite your answer using ONLY Latin/English letters in {'ENGLISH' if target_lang == 'ENGLISH' else 'ROMAN URDU'}.\n"
            f"DO NOT output Hindi or Urdu script characters under any circumstances.\n\n"
            f"Original patient question: {payload.question}\n"
            f"Previous prohibited attempt: {answer[:300]}"
        )
        try:
            if "gemini" in chat_model.lower():
                retry_usage = call_gemini_text(correction_prompt, model=chat_model)
            else:
                retry_usage = call_groq_text(correction_prompt, model=chat_model)
            retry_answer = (retry_usage.get("raw_text") or "").strip()
            if retry_answer and not contains_forbidden_script(retry_answer):
                answer = retry_answer
                usage = retry_usage
        except Exception:
            pass

    if not answer:
        raise HTTPException(status_code=502, detail="Chat model returned an empty answer.")

    try:
        save_chat_turn(
            assessment_id=payload.assessment_id,
            email=payload.email,
            phone=payload.phone,
            question=payload.question,
            answer=answer,
        )
    except Exception as e:
        print("save_chat_turn exception:", type(e), e)

    return {
        "answer": answer,
        "target_language": target_lang,
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
    findings_json: Optional[str] = Form(None),
    assessment_id: Optional[str] = Form(None),
):
    front_bytes = await front_image.read()
    if not front_bytes:
        raise HTTPException(status_code=400, detail="Front smile image was empty.")

    try:
        data_url = create_smile_simulation(
            front_bytes,
            report_text=report_text,
            findings_json=findings_json,
            mime_type=front_image.content_type or "image/jpeg",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not create simulation: {type(e).__name__}: {e}",
        )

    if assessment_id and db_ready():
        try:
            from db import get_supabase
            sb = get_supabase()
            res = sb.table("assessments").select("*").eq("id", assessment_id).limit(1).execute()
            if res.data:
                report = res.data[0]
                import base64
                sim_bytes = None
                if "," in data_url:
                    header, encoded = data_url.split(",", 1)
                    sim_bytes = base64.b64decode(encoded)
                else:
                    sim_bytes = base64.b64decode(data_url)

                if sim_bytes:
                    from photo_storage import upload_simulation_photo, download_assessment_photo_bytes
                    sim_path = upload_simulation_photo(assessment_id, sim_bytes)
                    if sim_path:
                        curr_findings = report.get("findings") or {}
                        if not isinstance(curr_findings, dict):
                            curr_findings = {}
                        curr_findings["photo_simulation_path"] = sim_path

                        sb.table("assessments").update({"findings": curr_findings}).eq("id", assessment_id).execute()

                        images_list = []
                        for label, col in (
                            ("Front smile", "photo_front_path"),
                            ("Left smile", "photo_left_path"),
                            ("Right smile", "photo_right_path"),
                        ):
                            path = str(report.get(col) or "").strip()
                            if path:
                                raw_bytes = download_assessment_photo_bytes(path)
                                if raw_bytes:
                                    images_list.append((label, raw_bytes))
                        images_list.append(("Simulation", sim_bytes))

                        from email_report import send_assessment_email
                        send_assessment_email(
                            to_email=report.get("email"),
                            overall_score=report.get("overall_score"),
                            findings=curr_findings,
                            report_text=report.get("report_text") or "",
                            category_scores=report.get("category_scores"),
                            images=images_list,
                            name=report.get("name"),
                            gender=report.get("gender"),
                            age=report.get("age"),
                            city=report.get("city"),
                        )
        except Exception as err:
            print(f"Failed to store simulation and resend email: {type(err).__name__}: {err}")

    engine = (os.getenv("SIMULATION_ENGINE") or "qwen").strip().lower()

    return {
        "image_data_url": data_url,
        "engine": engine,
        "disclaimer": (
            "Illustrative simulation only: treatments from your report applied as an edit "
            "of the uploaded photo. Gap fills match existing teeth where shown. "
            "Not a guaranteed clinical result. A dentist consultation is required for treatment planning."
        ),
        "report_context_used": bool(report_text or findings_json),
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

    frontend = str(FRONTEND_DIR)
    backend = str(Path(__file__).resolve().parent)
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[backend, frontend],
    )

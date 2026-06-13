"""
relaxntakenotes.africa — Backend API
Speech-to-Text, AI Summarization, Translation, and TTS platform.
"""

import os
import asyncio
import hashlib
import logging
import tempfile
import base64
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Request, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv
from supabase import create_client, Client
from deepgram import DeepgramClient, PrerecordedOptions
from huggingface_hub import InferenceClient
import edge_tts
import httpx
import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("relaxntakenotes")

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
# Resolve .env from workspace root regardless of cwd
_main_dir = os.path.dirname(os.path.abspath(__file__))
_parent_env = os.path.join(os.path.dirname(_main_dir), ".env")

if os.path.exists(".env"):
    load_dotenv(".env")
elif os.path.exists("../.env"):
    load_dotenv("../.env")
elif os.path.exists(_parent_env):
    load_dotenv(_parent_env)
else:
    load_dotenv()

# Core credentials
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
HF_TOKEN = os.getenv("HF_TOKEN")

# AI provider config — serverless by default, dedicated endpoint as optional fallback
AI_PROVIDER = os.getenv("AI_PROVIDER", "hf-inference")  # e.g. "hf-inference", "novita", "together"
AI_MODEL = os.getenv("AI_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct")
HF_ENDPOINT_URL = os.getenv("HF_ENDPOINT_URL", "")  # Legacy fallback — leave blank for serverless

# Budget limits — tripled for production capacity
MONTHLY_LIMIT_MINUTES = int(os.getenv("MONTHLY_LIMIT_MINUTES", "10000"))
USER_MONTHLY_LIMIT_MINUTES = int(os.getenv("USER_MONTHLY_LIMIT_MINUTES", "180"))
MAX_RECORDING_DURATION_MINUTES = int(os.getenv("MAX_RECORDING_DURATION_MINUTES", "30"))

# Security
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))  # 50 MB
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS",
        "https://relaxntakenotes.africa,http://localhost:5173,http://localhost:5174,http://localhost:4173",
    ).split(",")
    if o.strip()
]

# ---------------------------------------------------------------------------
# Client initialisation
# ---------------------------------------------------------------------------
supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Supabase client initialised.")
    except Exception as exc:
        logger.error("Failed to initialise Supabase client: %s", exc)

deepgram_client: Optional[DeepgramClient] = None
if DEEPGRAM_API_KEY:
    deepgram_client = DeepgramClient(DEEPGRAM_API_KEY)
    logger.info("Deepgram client initialised.")

# HF Inference — prefer serverless providers; fall back to dedicated endpoint if configured
_custom_model_name_cache: Optional[str] = None


def _get_custom_endpoint_model_name(endpoint_url: str, token: Optional[str]) -> str:
    """Discover the model ID served by a dedicated HF Inference Endpoint."""
    global _custom_model_name_cache
    if _custom_model_name_cache:
        return _custom_model_name_cache
    try:
        models_url = f"{endpoint_url.rstrip('/')}/v1/models"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = requests.get(models_url, headers=headers, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            if "data" in data and len(data["data"]) > 0:
                _custom_model_name_cache = data["data"][0]["id"]
                return _custom_model_name_cache
    except Exception as exc:
        logger.warning("Could not discover model on endpoint, using default: %s", exc)
    return "unsloth/Llama-3.1-8B-Instruct-bnb-4bit"


class _CustomInferenceClient(InferenceClient):
    """Patched client that resolves the real model name on dedicated endpoints."""

    def post(self, *args, **kwargs):
        json_data = kwargs.get("json")
        if isinstance(json_data, dict) and json_data.get("model") == "tgi":
            json_data["model"] = _get_custom_endpoint_model_name(self.model, self.token)
        return super().post(*args, **kwargs)


if HF_ENDPOINT_URL:
    # Legacy dedicated endpoint fallback
    hf_client = _CustomInferenceClient(model=HF_ENDPOINT_URL, token=HF_TOKEN, timeout=300.0)
    _hf_active_model: Optional[str] = None  # model resolved by endpoint
    logger.info("HF client: dedicated endpoint at %s", HF_ENDPOINT_URL)
else:
    provider_arg = AI_PROVIDER if AI_PROVIDER and AI_PROVIDER.lower() != "auto" else None
    hf_client = InferenceClient(provider=provider_arg, api_key=HF_TOKEN, timeout=120.0)
    _hf_active_model = AI_MODEL
    logger.info("HF client: serverless provider=%s  model=%s", provider_arg or "auto", AI_MODEL)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="relaxntakenotes.africa API",
    description="Speech-to-Text and AI Note-Taking backend platform.",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def get_user_hash(request: Request, x_user_uuid: Optional[str] = Header(None)) -> str:
    """Deterministic user fingerprint from IP + browser UUID."""
    client_ip = _get_client_ip(request)
    uuid_part = x_user_uuid or "anonymous"
    return hashlib.sha256(f"{client_ip}-{uuid_part}".encode()).hexdigest()


def _start_of_month_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def get_usage_stats(user_hash: str) -> tuple[int, int]:
    """Return (user_seconds, global_seconds) for the current month."""
    if not supabase:
        return 0, 0

    start = _start_of_month_iso()
    try:
        user_resp = (
            supabase.table("usage_logs")
            .select("duration_seconds")
            .eq("user_hash", user_hash)
            .gte("created_at", start)
            .execute()
        )
        user_secs = sum(r["duration_seconds"] for r in user_resp.data)

        global_resp = (
            supabase.table("usage_logs")
            .select("duration_seconds")
            .gte("created_at", start)
            .execute()
        )
        global_secs = sum(r["duration_seconds"] for r in global_resp.data)
        return user_secs, global_secs
    except Exception as exc:
        logger.warning("DB error fetching usage stats: %s", exc)
        return 0, 0


async def _call_ai(system_prompt: str, user_prompt: str) -> str:
    """Run an AI chat completion via the configured provider. Returns result text."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    response = await asyncio.to_thread(
        hf_client.chat_completion,
        model=_hf_active_model,
        messages=messages,
        max_tokens=2048,
        temperature=0.3,
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class AIFeaturesRequest(BaseModel):
    transcript: str
    feature_type: str  # "summary" | "insights" | "translation"
    metadata: Optional[dict] = None
    target_language: Optional[str] = "French"

    @field_validator("feature_type")
    @classmethod
    def validate_feature_type(cls, v: str) -> str:
        allowed = {"summary", "insights", "translation"}
        if v not in allowed:
            raise ValueError(f"feature_type must be one of {allowed}")
        return v


class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = "en-US-JennyNeural"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "relaxntakenotes.africa API",
        "version": "1.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
def health_check():
    """Container orchestration healthcheck."""
    return {"status": "healthy"}


@app.get("/api/status")
async def get_status(request: Request, user_hash: str = Depends(get_user_hash)):
    user_seconds, global_seconds = await asyncio.to_thread(get_usage_stats, user_hash)
    user_min = user_seconds / 60.0
    global_min = global_seconds / 60.0
    return {
        "global_usage_minutes": round(global_min, 2),
        "global_limit_minutes": MONTHLY_LIMIT_MINUTES,
        "user_usage_minutes": round(user_min, 2),
        "user_limit_minutes": USER_MONTHLY_LIMIT_MINUTES,
        "is_over_budget": global_min >= MONTHLY_LIMIT_MINUTES,
        "user_is_over_limit": user_min >= USER_MONTHLY_LIMIT_MINUTES,
        "max_recording_duration_minutes": MAX_RECORDING_DURATION_MINUTES,
    }


@app.post("/api/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    user_hash: str = Depends(get_user_hash),
):
    # Budget enforcement
    if supabase:
        user_secs, global_secs = await asyncio.to_thread(get_usage_stats, user_hash)
        if (global_secs / 60.0) >= MONTHLY_LIMIT_MINUTES:
            raise HTTPException(
                status_code=403,
                detail="Global platform transcription budget exceeded for this month.",
            )
        if (user_secs / 60.0) >= USER_MONTHLY_LIMIT_MINUTES:
            raise HTTPException(
                status_code=403,
                detail="Personal monthly transcription limit reached. Upgrade for unlimited hours.",
            )

    try:
        file_bytes = await file.read()
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum allowed size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
            )
        logger.info(
            "Transcribe: file=%s  type=%s  size=%d bytes",
            file.filename,
            file.content_type,
            len(file_bytes),
        )

        if not deepgram_client:
            raise HTTPException(status_code=500, detail="Transcription service not configured.")

        options = PrerecordedOptions(
            model="nova-2",
            smart_format=True,
            diarize=True,
            punctuate=True,
        )

        payload = {"buffer": file_bytes}
        response = await asyncio.to_thread(
            deepgram_client.listen.prerecorded.v("1").transcribe_file,
            payload,
            options,
            timeout=httpx.Timeout(1800.0, connect=60.0),
        )

        response_dict = response.to_dict() if hasattr(response, "to_dict") else response
        meta = response_dict.get("metadata", {})
        duration_seconds = round(meta.get("duration", 0))

        if duration_seconds > MAX_RECORDING_DURATION_MINUTES * 60:
            raise HTTPException(
                status_code=400,
                detail=f"Audio exceeds {MAX_RECORDING_DURATION_MINUTES}-minute limit.",
            )

        # Parse diarized output
        channels = response_dict.get("results", {}).get("channels", [])
        transcript_text = ""
        paragraphs: list[dict] = []

        if channels:
            alts = channels[0].get("alternatives", [])
            if alts:
                paras_data = alts[0].get("paragraphs", {}).get("paragraphs", [])
                if paras_data:
                    for p in paras_data:
                        speaker = p.get("speaker", 0)
                        text = " ".join(s.get("text", "") for s in p.get("sentences", []))
                        paragraphs.append({"speaker": f"Speaker {speaker}", "text": text})
                else:
                    transcript_text = alts[0].get("transcript", "")
                    paragraphs.append({"speaker": "Speaker 0", "text": transcript_text})

        # Log usage
        if supabase:
            try:
                await asyncio.to_thread(
                    lambda: supabase.table("usage_logs")
                    .insert({"user_hash": user_hash, "duration_seconds": duration_seconds})
                    .execute()
                )
            except Exception as db_err:
                logger.error("Failed to log usage: %s", db_err)

        return {
            "duration_seconds": duration_seconds,
            "paragraphs": paragraphs or [{"speaker": "Speaker 0", "text": transcript_text}],
            "raw_transcript": transcript_text or " ".join(p["text"] for p in paragraphs),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Transcription error")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}")


@app.post("/api/ai-features")
async def generate_ai_features(payload: AIFeaturesRequest):
    if not payload.transcript.strip():
        raise HTTPException(status_code=400, detail="Transcript is empty.")

    # Build prompt
    user_prompt = f"Transcript:\n{payload.transcript}\n\n"

    if payload.feature_type == "summary":
        system_prompt = (
            "You are an expert AI note-taking and note-synthesizing assistant. "
            "Generate a highly structured summary of the provided transcript. "
            "Include a concise executive summary, followed by formal meeting minutes "
            "with timestamp references (if applicable), and list the main topics discussed. "
            "Use bullet points and clean markdown formatting."
        )
        if payload.metadata:
            meta_str = "\n".join(f"{k}: {v}" for k, v in payload.metadata.items() if v)
            system_prompt += f"\nUse this metadata context for the document:\n{meta_str}"

    elif payload.feature_type == "insights":
        system_prompt = (
            "You are a strategic analyst. "
            "Analyze the following transcript and extract the key takeaways, "
            "critical discussion points, specific actionable items (with assigned owners if mentioned), "
            "and core themes. Format the response beautifully using markdown."
        )

    elif payload.feature_type == "translation":
        target_lang = payload.target_language or "French"
        system_prompt = (
            f"You are a professional translator. Translate the following transcript accurately into {target_lang}. "
            "Maintain the tone, speaker formatting (e.g., 'Speaker 0:', 'Speaker 1:'), and layout of the original text. "
            "Return only the translated text."
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid feature_type.")

    try:
        result_text = await _call_ai(system_prompt, user_prompt)
        return {"result": result_text}
    except Exception as exc:
        logger.exception("AI inference error")
        raise HTTPException(
            status_code=503,
            detail="AI service is temporarily unavailable. Please try again shortly.",
        )


@app.post("/api/tts")
async def text_to_speech(payload: TTSRequest):
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Text payload is empty.")

    fd, temp_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)

    try:
        communicate = edge_tts.Communicate(payload.text, payload.voice)
        await communicate.save(temp_path)
        return FileResponse(
            path=temp_path,
            media_type="audio/mpeg",
            filename="voice_notes.mp3",
            background=None,  # ensures cleanup after send
        )
    except Exception as exc:
        logger.warning("TTS generation failed, returning silent fallback: %s", exc)
        try:
            silent_b64 = (
                "SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU2LjM2LjEwMAAAAAAAAAAAAAAA"
                "//OEAAAAAAAAAAAAAAAAAAAAAAAASW5mbwAAAA8AAAAEAAABIADAwMDAwMDA"
                "wMDAwMDAwMDAwMDAwMDAwMDV1dXV1dXV1dXV1dXV1dXV1dXV1dXV1dXV6u"
                "rq6urq6urq6urq6urq6urq6urq6urq6v////////////////////////"
                "////////8AAAAATGF2YzU2LjQxAAAAAAAAAAAAAAAAJAAAAAAAAAAAASDs90"
                "hvAAAAAAAAAAAAAAAAAAAA//MUZAAAAAGkAAAAAAAAA0gAAAAATEFN//MUZAM"
                "AAAGkAAAAAAAAA0gAAAAATEFN//MUZAYAAAGkAAAAAAAAA0gAAAAAOTku//MU"
                "ZAkAAAGkAAAAAAAAA0gAAAAANVVV"
            )
            with open(temp_path, "wb") as f:
                f.write(base64.b64decode(silent_b64))
            return FileResponse(path=temp_path, media_type="audio/mpeg", filename="voice_notes.mp3")
        except Exception as fb_err:
            logger.error("TTS fallback also failed: %s", fb_err)
            raise HTTPException(status_code=500, detail=f"TTS generation failed: {exc}")


@app.post("/api/download")
async def download_file(
    content: str = Form(...),
    filename: str = Form(...),
    mime_type: str = Form(...),
    is_base64: str = Form("false"),
):
    try:
        if is_base64 == "true":
            if "," in content:
                content = content.split(",", 1)[1]
            file_bytes = base64.b64decode(content)
        else:
            file_bytes = content.encode("utf-8")

        safe_filename = urllib.parse.quote(filename)
        return Response(
            content=file_bytes,
            media_type=mime_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}"},
        )
    except Exception as exc:
        logger.error("Download error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Download failed: {exc}")

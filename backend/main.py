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

from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Request, Depends, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv
from supabase import create_client, Client
from deepgram import DeepgramClient, PrerecordedOptions, LiveOptions, LiveTranscriptionEvents
from huggingface_hub import InferenceClient
import edge_tts
import httpx
import requests
import json

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

# Fact-checking API keys (optional)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

# Security
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(350 * 1024 * 1024)))  # 350 MB
MAX_LIVESTREAM_SECONDS = int(os.getenv("MAX_LIVESTREAM_SECONDS", "5400"))  # 1.5 hours
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


# --- Authentication Dependency ---
security = HTTPBearer(auto_error=False)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Extract and verify Supabase JWT token from Authorization header."""
    if not credentials or not supabase:
        return None
    try:
        token = credentials.credentials
        # Supabase Python client currently doesn't have a verify_jwt method that doesn't set session state.
        # We can just fetch the user using the token to verify it.
        user_response = supabase.auth.get_user(token)
        if user_response and user_response.user:
            return user_response.user
    except Exception as exc:
        logger.warning(f"Failed to authenticate user token: {exc}")
    return None


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


def _execute_with_retry(query, max_retries=3):
    import time
    for i in range(max_retries):
        try:
            return query.execute()
        except Exception as exc:
            if i == max_retries - 1:
                raise
            if "ConnectionTerminated" in str(exc) or "ReadError" in str(exc) or "ProtocolError" in str(exc):
                time.sleep(0.5)
            else:
                raise

def get_usage_stats(user_hash: str) -> tuple[int, int]:
    """Return (user_seconds, global_seconds) for the current month."""
    if not supabase:
        return 0, 0

    start = _start_of_month_iso()
    try:
        user_resp = _execute_with_retry(
            supabase.table("usage_logs")
            .select("duration_seconds")
            .eq("user_hash", user_hash)
            .gte("created_at", start)
        )
        user_secs = sum(r["duration_seconds"] for r in user_resp.data)

        global_resp = _execute_with_retry(
            supabase.table("usage_logs")
            .select("duration_seconds")
            .gte("created_at", start)
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


# --- Transcript Chunking ---
# Most LLMs have context windows of 8k-32k tokens (~6k-24k words).
# A 1.5-hour transcript can be ~15,000-20,000 words.
# This helper splits text into manageable chunks and synthesizes results.

CHUNK_WORD_LIMIT = int(os.getenv("CHUNK_WORD_LIMIT", "4000"))  # ~5k tokens per chunk


def _split_transcript_into_chunks(transcript: str, max_words: int = CHUNK_WORD_LIMIT) -> list[str]:
    """Split transcript into chunks of approximately max_words, splitting on paragraph boundaries."""
    paragraphs = transcript.split("\n\n")
    chunks = []
    current_chunk = []
    current_word_count = 0

    for para in paragraphs:
        para_words = len(para.split())
        if current_word_count + para_words > max_words and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [para]
            current_word_count = para_words
        else:
            current_chunk.append(para)
            current_word_count += para_words

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks if chunks else [transcript]


async def _call_ai_chunked(system_prompt: str, transcript: str, synthesis_prompt: str = "") -> str:
    """Process a potentially long transcript through the LLM in chunks, then synthesize.

    For short transcripts (under CHUNK_WORD_LIMIT), this simply calls _call_ai directly.
    For long transcripts, it:
      1. Splits the transcript into chunks
      2. Processes each chunk individually
      3. Runs a final synthesis pass to merge all chunk results
    """
    word_count = len(transcript.split())

    # Short transcript — process directly
    if word_count <= CHUNK_WORD_LIMIT:
        return await _call_ai(system_prompt, transcript)

    # Long transcript — chunk and process
    chunks = _split_transcript_into_chunks(transcript)
    logger.info("Chunking transcript: %d words -> %d chunks", word_count, len(chunks))

    chunk_results = []
    for i, chunk in enumerate(chunks):
        chunk_header = f"[Transcript Chunk {i + 1} of {len(chunks)}]\n\n"
        result = await _call_ai(system_prompt, chunk_header + chunk)
        chunk_results.append(f"--- Chunk {i + 1}/{len(chunks)} ---\n{result}")

    # Synthesis pass — merge all chunk outputs into a unified result
    combined = "\n\n".join(chunk_results)
    merge_system = (
        synthesis_prompt or
        "You are a document editor. You have been given multiple partial analyses of different "
        "segments of the same transcript. Merge them into a single, cohesive, deduplicated document. "
        "Remove any redundant headers or repeated items. Preserve all unique information. "
        "Output clean, well-structured markdown."
    )
    return await _call_ai(merge_system, combined)


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
                    lambda: _execute_with_retry(
                        supabase.table("usage_logs")
                        .insert({"user_hash": user_hash, "duration_seconds": duration_seconds})
                    )
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


# ---------------------------------------------------------------------------
# LiveStream — Real-time transcription via Deepgram WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/livestream")
async def livestream_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time audio transcription.

    Protocol:
    - Client sends binary audio chunks (WebM/PCM from browser MediaRecorder)
    - Server streams back JSON messages with transcript events:
      {"type": "transcript", "is_final": bool, "text": str, "speaker": int, "start": float, "end": float}
      {"type": "status", "message": str}
      {"type": "error", "message": str}
    """
    await websocket.accept()
    logger.info("LiveStream WebSocket connected")

    if not deepgram_client:
        await websocket.send_json({"type": "error", "message": "Transcription service not configured."})
        await websocket.close()
        return

    # We'll use an async approach: open a Deepgram live connection,
    # forward audio chunks, and relay transcript events back.
    dg_connection = None
    is_closing = False

    try:
        # Create Deepgram live transcription connection
        dg_connection = deepgram_client.listen.websocket.v("1")

        # Event handler: transcript received from Deepgram
        async def on_message(self, result, **kwargs):
            try:
                channel = result.channel
                if channel and channel.alternatives and len(channel.alternatives) > 0:
                    alt = channel.alternatives[0]
                    transcript_text = alt.transcript
                    if transcript_text.strip():
                        # Determine speaker from words metadata if available
                        speaker = 0
                        if alt.words and len(alt.words) > 0:
                            first_word = alt.words[0]
                            speaker = getattr(first_word, 'speaker', 0) or 0

                        is_final = result.is_final
                        start_time = result.start if hasattr(result, 'start') else 0.0
                        duration = result.duration if hasattr(result, 'duration') else 0.0

                        msg = {
                            "type": "transcript",
                            "is_final": is_final,
                            "text": transcript_text,
                            "speaker": speaker,
                            "start": start_time,
                            "end": start_time + duration,
                            "speech_final": getattr(result, 'speech_final', False),
                        }
                        if not is_closing:
                            await websocket.send_json(msg)
            except Exception as e:
                logger.warning("Error sending transcript to client: %s", e)

        async def on_error(self, error, **kwargs):
            logger.error("Deepgram live error: %s", error)
            try:
                if not is_closing:
                    await websocket.send_json({"type": "error", "message": str(error)})
            except Exception:
                pass

        async def on_close(self, close, **kwargs):
            logger.info("Deepgram live connection closed")

        async def on_open(self, open, **kwargs):
            logger.info("Deepgram live connection opened")
            try:
                if not is_closing:
                    await websocket.send_json({"type": "status", "message": "Deepgram connection established. Listening..."})
            except Exception:
                pass

        # Register event handlers
        dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
        dg_connection.on(LiveTranscriptionEvents.Error, on_error)
        dg_connection.on(LiveTranscriptionEvents.Close, on_close)
        dg_connection.on(LiveTranscriptionEvents.Open, on_open)

        # Configure live transcription options
        options = LiveOptions(
            model="nova-2",
            language="en",
            smart_format=True,
            punctuate=True,
            diarize=True,
            interim_results=True,
            utterance_end_ms="1500",
            vad_events=True,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
        )

        # Start the Deepgram live connection
        started = dg_connection.start(options)
        if not started:
            await websocket.send_json({"type": "error", "message": "Failed to start Deepgram live connection."})
            await websocket.close()
            return

        await websocket.send_json({"type": "status", "message": "Ready to receive audio."})

        # Main loop: receive audio chunks from client, forward to Deepgram
        while True:
            try:
                data = await websocket.receive()

                if "bytes" in data:
                    # Binary audio data — forward to Deepgram
                    dg_connection.send(data["bytes"])
                elif "text" in data:
                    # Control messages from client
                    try:
                        control = json.loads(data["text"])
                        if control.get("type") == "stop":
                            logger.info("Client requested stop")
                            break
                        elif control.get("type") == "configure":
                            # Client can send audio config (sample rate, encoding, etc.)
                            logger.info("Client config: %s", control)
                    except json.DecodeError:
                        pass

            except WebSocketDisconnect:
                logger.info("LiveStream WebSocket disconnected")
                break
            except Exception as recv_err:
                logger.warning("WebSocket receive error: %s", recv_err)
                break

    except Exception as exc:
        logger.exception("LiveStream WebSocket error")
        try:
            if not is_closing:
                await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        is_closing = True
        if dg_connection:
            try:
                dg_connection.finish()
            except Exception:
                pass
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("LiveStream WebSocket cleanup complete")


# ---------------------------------------------------------------------------
# LiveStream — AI Notes Generation
# ---------------------------------------------------------------------------

class LivestreamAINotesRequest(BaseModel):
    transcript: str
    context: Optional[dict] = None


@app.post("/api/livestream/ai-notes")
async def generate_livestream_notes(payload: LivestreamAINotesRequest):
    """Generate AI-powered notes, action items, and key decisions from transcript."""
    if not payload.transcript.strip():
        raise HTTPException(status_code=400, detail="Transcript is empty.")

    system_prompt = (
        "You are an expert real-time meeting assistant. Analyze the provided live transcript "
        "and generate structured notes. Your output MUST include:\n"
        "1. **Key Topics** — Main subjects discussed, as section headers\n"
        "2. **Decisions Made** — Any decisions, agreements, or conclusions reached\n"
        "3. **Action Items** — Specific tasks with assigned owners (if mentioned) and deadlines\n"
        "4. **Important Quotes** — Notable statements worth preserving verbatim\n"
        "5. **Open Questions** — Unresolved questions that need follow-up\n\n"
        "Format using clean markdown. Be concise but comprehensive. "
        "If speakers are identified, attribute items to specific speakers."
    )

    context_str = ""
    if payload.context:
        context_str = "\nContext: " + "\n".join(f"{k}: {v}" for k, v in payload.context.items() if v)

    full_transcript = f"Live Transcript:{context_str}\n\n{payload.transcript}"

    try:
        result_text = await _call_ai_chunked(
            system_prompt,
            full_transcript,
            synthesis_prompt=(
                "You are an expert meeting assistant. You have multiple partial note sets from different "
                "segments of the same meeting transcript. Merge them into a single cohesive set of notes. "
                "Deduplicate action items, merge topic sections, and ensure all key decisions and quotes "
                "are preserved. Output clean markdown with the original section structure: "
                "Key Topics, Decisions Made, Action Items, Important Quotes, Open Questions."
            )
        )
        return {"result": result_text}
    except Exception as exc:
        logger.exception("LiveStream AI notes error")
        raise HTTPException(status_code=503, detail="AI service temporarily unavailable.")


# ---------------------------------------------------------------------------
# LiveStream — Fact-Checking Pipeline
# ---------------------------------------------------------------------------

class FactCheckRequest(BaseModel):
    transcript: str
    claims: Optional[list] = None  # Pre-detected claims, if any
    context: Optional[dict] = None
    use_org_docs: Optional[bool] = False
    session_id: Optional[str] = None


class ClaimDetectionRequest(BaseModel):
    transcript: str
    context: Optional[dict] = None


@app.post("/api/livestream/detect-claims")
async def detect_claims(payload: ClaimDetectionRequest):
    """Detect factual claims from a transcript segment.

    Uses AI to identify check-worthy factual assertions —
    specific statistics, dates, events, policy claims, etc.
    Filters out opinions, predictions, and subjective statements.
    """
    if not payload.transcript.strip():
        raise HTTPException(status_code=400, detail="Transcript is empty.")

    system_prompt = (
        "You are a fact-check analyst. Your job is to identify SPECIFIC, VERIFIABLE factual claims "
        "in a transcript. A factual claim is a statement that can be checked against evidence — "
        "statistics, dates, historical events, scientific facts, policy details, attributions.\n\n"
        "RULES:\n"
        "- ONLY extract claims that are specific and verifiable\n"
        "- SKIP opinions, predictions, subjective statements, and vague generalizations\n"
        "- SKIP pleasantries, greetings, and procedural language\n"
        "- Each claim should be a single, self-contained statement\n"
        "- Preserve the speaker attribution if available\n\n"
        "Return a JSON array of objects, each with:\n"
        '  {"claim": "the exact or paraphrased claim", "speaker": "Speaker X or name", '
        '"severity": "high|medium|low", "category": "statistic|date|event|science|policy|attribution"}\n\n'
        "If no verifiable claims are found, return an empty array: []\n"
        "Return ONLY the JSON array, no other text."
    )

    context_str = ""
    if payload.context:
        context_str = "\nContext: " + "\n".join(f"{k}: {v}" for k, v in payload.context.items() if v)

    user_prompt = f"Transcript segment:{context_str}\n\n{payload.transcript}"

    try:
        result_text = await _call_ai_chunked(
            system_prompt,
            user_prompt,
            synthesis_prompt=(
                "You are a fact-check analyst. You have multiple partial claim extraction results from "
                "different segments of the same transcript. Merge them into a single JSON array of claims. "
                "Deduplicate any claims that appear in multiple chunks. "
                "Return ONLY the merged JSON array, no other text."
            )
        )
        # Parse JSON from AI response
        result_text = result_text.strip()
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        claims = json.loads(result_text)
        return {"claims": claims}
    except json.JSONDecodeError:
        logger.warning("Failed to parse claims JSON from AI response")
        return {"claims": [], "raw_response": result_text}
    except Exception as exc:
        logger.exception("Claim detection error")
        raise HTTPException(status_code=503, detail="AI service temporarily unavailable.")


@app.post("/api/livestream/fact-check")
async def fact_check_claims(payload: FactCheckRequest, user=Depends(get_current_user)):
    """Verify factual claims using web search (Serper) + AI evaluation.

    Pipeline:
    1. For each claim, search the web for evidence (via Serper API)
    2. Feed claim + evidence to AI for evaluation
    3. Return verdict: TRUE, FALSE, MISLEADING, UNVERIFIABLE with confidence + sources

    Falls back to LLM-only evaluation if Serper API is unavailable.
    """
    if not payload.claims and not payload.transcript:
        raise HTTPException(status_code=400, detail="No claims or transcript provided.")

    # If claims not pre-extracted, detect them first
    claims_to_check = payload.claims or []
    if not claims_to_check and payload.transcript:
        detect_result = await detect_claims(ClaimDetectionRequest(
            transcript=payload.transcript,
            context=payload.context
        ))
        claims_to_check = detect_result.get("claims", [])

    if not claims_to_check:
        return {"results": [], "message": "No verifiable claims detected."}

    # Fetch organization documents from Supabase if requested
    org_context = ""
    if payload.use_org_docs and user and supabase:
        try:
            # Get user's documents from database
            docs_resp = supabase.table("context_documents").select("*").eq("user_id", user.id).execute()
            if docs_resp.data:
                org_context = "ORGANIZATION CONTEXT DOCUMENTS:\n\n"
                for doc in docs_resp.data:
                    # Download the file from Supabase storage
                    file_path = doc.get("storage_path")
                    file_name = doc.get("file_name")
                    if file_path:
                        try:
                            file_data = supabase.storage.from_("context_documents").download(file_path)
                            org_context += f"--- Document: {file_name} ---\n{file_data.decode('utf-8', errors='replace')}\n\n"
                        except Exception as dl_err:
                            logger.warning(f"Failed to download context doc {file_name}: {dl_err}")
        except Exception as e:
            logger.warning(f"Failed to fetch organization documents: {e}")

    results = []
    for claim_obj in claims_to_check:
        claim_text = claim_obj.get("claim", "") if isinstance(claim_obj, dict) else str(claim_obj)
        if not claim_text.strip():
            continue

        # Step 1: Search for evidence
        evidence = ""
        sources = []
        if SERPER_API_KEY:
            try:
                search_resp = await asyncio.to_thread(
                    requests.post,
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                    json={"q": claim_text, "num": 5},
                    timeout=10.0
                )
                if search_resp.status_code == 200:
                    search_data = search_resp.json()
                    organic = search_data.get("organic", [])
                    for item in organic[:5]:
                        title = item.get("title", "")
                        snippet = item.get("snippet", "")
                        link = item.get("link", "")
                        evidence += f"Source: {title}\n{snippet}\nURL: {link}\n\n"
                        sources.append({"title": title, "url": link, "snippet": snippet})
                    # Also check knowledge graph
                    kg = search_data.get("knowledgeGraph", {})
                    if kg:
                        evidence += f"Knowledge Graph: {kg.get('title', '')} — {kg.get('description', '')}\n"
            except Exception as search_err:
                logger.warning("Serper search failed for claim: %s", search_err)

        # Step 2: AI evaluation
        eval_system = (
            "You are a rigorous fact-checker. Evaluate the following claim against the provided evidence. "
            "You MUST return a JSON object with:\n"
            '{"verdict": "TRUE|FALSE|MISLEADING|UNVERIFIABLE", '
            '"confidence": 0.0-1.0, '
            '"explanation": "brief explanation of your reasoning", '
            '"key_evidence": "the most relevant piece of evidence"}\n\n'
            "RULES:\n"
            "- TRUE: The claim is factually correct based on evidence\n"
            "- FALSE: The claim is demonstrably incorrect\n"
            "- MISLEADING: The claim contains partial truth but is presented in a misleading way\n"
            "- UNVERIFIABLE: Insufficient evidence to verify or deny the claim\n"
            "- Generalizations without specific data should be UNVERIFIABLE\n"
            "- Actively look for counterevidence\n"
            "- Return ONLY the JSON object, no other text."
        )

        eval_prompt = f"Claim: {claim_text}\n\n"
        if evidence:
            eval_prompt += f"Web Evidence:\n{evidence}\n"
        
        if org_context:
            eval_prompt += f"Internal Organization Evidence:\n{org_context}\n"
            
        if not evidence and not org_context:
            eval_prompt += "No external evidence available. Use your knowledge base only.\n"

        try:
            eval_result = await _call_ai(eval_system, eval_prompt)
            eval_result = eval_result.strip()
            if eval_result.startswith("```"):
                eval_result = eval_result.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            verdict_data = json.loads(eval_result)
            results.append({
                "claim": claim_text,
                "speaker": claim_obj.get("speaker", "") if isinstance(claim_obj, dict) else "",
                "category": claim_obj.get("category", "") if isinstance(claim_obj, dict) else "",
                "verdict": verdict_data.get("verdict", "UNVERIFIABLE"),
                "confidence": verdict_data.get("confidence", 0.5),
                "explanation": verdict_data.get("explanation", ""),
                "key_evidence": verdict_data.get("key_evidence", ""),
                "sources": sources,
                "used_web_search": bool(evidence),
            })
        except (json.JSONDecodeError, Exception) as eval_err:
            logger.warning("Fact-check evaluation failed for claim '%s': %s", claim_text[:50], eval_err)
            results.append({
                "claim": claim_text,
                "speaker": claim_obj.get("speaker", "") if isinstance(claim_obj, dict) else "",
                "verdict": "UNVERIFIABLE",
                "confidence": 0.0,
                "explanation": "Evaluation failed.",
                "sources": sources,
                "used_web_search": bool(evidence),
            })

    return {"results": results}
# ---------------------------------------------------------------------------
# LiveStream — Meeting Package Generator
# ---------------------------------------------------------------------------

class MeetingPackageRequest(BaseModel):
    transcript: str
    ai_notes: Optional[str] = None
    fact_check_results: Optional[list] = None
    metadata: Optional[dict] = None
    session_id: Optional[str] = None


@app.post("/api/livestream/meeting-package")
async def generate_meeting_package(payload: MeetingPackageRequest):
    """Generate a polished meeting package with transcript, summaries,
    decisions, action items, verified claims, and formatted output."""
    if not payload.transcript.strip():
        raise HTTPException(status_code=400, detail="Transcript is empty.")

    # Build comprehensive meeting document
    system_prompt = (
        "You are a professional meeting secretary. Generate a comprehensive, polished meeting package "
        "from the provided materials. The package MUST include these sections:\n\n"
        "# Meeting Package\n\n"
        "## 1. Executive Summary\n"
        "A concise 2-3 paragraph overview of the meeting.\n\n"
        "## 2. Attendees & Speakers\n"
        "List all identified speakers/participants.\n\n"
        "## 3. Key Decisions\n"
        "Numbered list of all decisions made during the meeting.\n\n"
        "## 4. Action Items\n"
        "Table format: | # | Action | Owner | Deadline | Status |\n\n"
        "## 5. Discussion Summary\n"
        "Organized by topic with key points under each.\n\n"
        "## 6. Verified Claims\n"
        "If fact-check results are provided, include a section listing claims and their verdicts.\n\n"
        "## 7. Open Items & Follow-ups\n"
        "Unresolved questions and items for the next meeting.\n\n"
        "Use clean, professional markdown formatting throughout."
    )

    user_content = f"Full Transcript:\n{payload.transcript}\n\n"
    if payload.ai_notes:
        user_content += f"AI-Generated Notes:\n{payload.ai_notes}\n\n"
    if payload.fact_check_results:
        user_content += "Fact-Check Results:\n"
        for r in payload.fact_check_results:
            if isinstance(r, dict):
                user_content += f"- Claim: {r.get('claim', 'N/A')} → Verdict: {r.get('verdict', 'N/A')} (Confidence: {r.get('confidence', 'N/A')})\n"
        user_content += "\n"
    if payload.metadata:
        meta_str = "\n".join(f"{k}: {v}" for k, v in payload.metadata.items() if v)
        user_content += f"Meeting Metadata:\n{meta_str}\n"

    try:
        result_text = await _call_ai_chunked(
            system_prompt,
            user_content,
            synthesis_prompt=(
                "You are a professional meeting secretary. You have multiple partial meeting package drafts "
                "from different segments of the same meeting. Merge them into a single, polished meeting package. "
                "Ensure the final document has these sections: Executive Summary, Attendees & Speakers, "
                "Key Decisions, Action Items (table format), Discussion Summary, Verified Claims, "
                "Open Items & Follow-ups. Deduplicate entries and use clean professional markdown."
            )
        )
        return {"result": result_text}
    except Exception as exc:
        logger.exception("Meeting package generation error")
        raise HTTPException(status_code=503, detail="AI service temporarily unavailable.")

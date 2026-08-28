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
import io
import uuid
import numpy as np
from pypdf import PdfReader
from docx import Document as DocxDocument

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
MAX_RECORDING_DURATION_MINUTES = int(os.getenv("MAX_RECORDING_DURATION_MINUTES", "120"))  # 2 hours

# Fact-checking API keys (optional)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

# Embedding model for support-document RAG
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# Registration confirmation gate — false for testing (auto-confirm + auto sign-in),
# set true before production launch to restore normal Supabase email confirmation.
REQUIRE_EMAIL_CONFIRMATION = os.getenv("REQUIRE_EMAIL_CONFIRMATION", "false").lower() == "true"

# AssemblyAI — alternative transcription provider for long-form audio
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY", "")
TRANSCRIPTION_PROVIDER = os.getenv("TRANSCRIPTION_PROVIDER", "auto").lower()  # auto | deepgram | assemblyai
AUTO_ROUTE_THRESHOLD_MB = int(os.getenv("AUTO_ROUTE_THRESHOLD_MB", "50"))
AUTO_ROUTE_THRESHOLD_BYTES = AUTO_ROUTE_THRESHOLD_MB * 1024 * 1024

# Audio chunking — Deepgram processes chunks in parallel; AssemblyAI is the fallback for very large files
AUDIO_CHUNK_MINUTES = int(os.getenv("AUDIO_CHUNK_MINUTES", "10"))            # each chunk length
ASSEMBLYAI_FALLBACK_MB = int(os.getenv("ASSEMBLYAI_FALLBACK_MB", "100"))     # above this → AssemblyAI directly
ASSEMBLYAI_FALLBACK_BYTES = ASSEMBLYAI_FALLBACK_MB * 1024 * 1024

# Subject Matter Specialization — model + prompt overrides per domain
# Each domain can optionally specify a dedicated AI model and domain-specific prompt prefixes
MEDICAL_AI_MODEL = os.getenv("MEDICAL_AI_MODEL", "")

SUBJECT_MATTER_CONFIG = {
    "General": {
        "model": None,  # Use default AI_MODEL
        "summary_prefix": "",
        "insights_prefix": "",
        "livestream_notes_prefix": "",
    },
    "Medical": {
        "model": MEDICAL_AI_MODEL or None,
        "summary_prefix": (
            "You are a medical transcription specialist with expertise in clinical documentation. "
            "Structure the summary using medical documentation standards:\n"
            "- **Chief Complaint / Reason for Visit** (if identifiable)\n"
            "- **History of Present Illness (HPI)**\n"
            "- **Assessment / Differential Diagnoses discussed**\n"
            "- **Plan / Recommended Actions**\n"
            "- **Medications mentioned** (with dosages if stated)\n"
            "- **Follow-up / Referrals**\n"
            "Use proper medical terminology. Flag any drug interactions or contraindications mentioned. "
            "IMPORTANT: This is an AI-assisted summary and should NOT replace professional medical judgment.\n\n"
        ),
        "insights_prefix": (
            "You are a clinical analyst reviewing a medical transcript. Extract:\n"
            "- **Clinical Findings** — symptoms, diagnoses, lab results discussed\n"
            "- **Medications & Dosages** — all drugs mentioned with context\n"
            "- **Treatment Decisions** — agreed-upon treatment paths\n"
            "- **Referrals & Follow-ups** — specialist referrals, next appointments\n"
            "- **Patient Safety Flags** — any allergies, contraindications, or warnings mentioned\n"
            "- **Unresolved Clinical Questions** — pending tests, uncertain diagnoses\n"
            "Use clinical terminology. Flag items requiring urgent attention.\n\n"
        ),
        "livestream_notes_prefix": (
            "You are a medical meeting assistant with expertise in clinical documentation. "
            "Analyze the transcript with a medical lens. In addition to the standard sections, include:\n"
            "- **Clinical Findings** — symptoms, diagnoses, lab results, vitals discussed\n"
            "- **Medications & Dosages** — all drugs mentioned with dosages and context\n"
            "- **Patient Safety Flags** — allergies, contraindications, drug interactions\n"
            "Use proper medical terminology throughout.\n\n"
        ),
    },
    "Legal": {
        "model": None,  # Placeholder — configure via LEGAL_AI_MODEL env var when ready
        "summary_prefix": (
            "You are a legal transcription specialist. Structure the summary highlighting:\n"
            "- **Case References & Citations** mentioned\n"
            "- **Legal Arguments & Positions** presented\n"
            "- **Rulings, Decisions & Orders** made\n"
            "- **Stipulations & Agreements** reached\n"
            "- **Deadlines & Filing Requirements** discussed\n"
            "Use precise legal terminology. Note any procedural motions.\n\n"
        ),
        "insights_prefix": (
            "You are a legal analyst. Extract:\n"
            "- **Key Legal Issues** — statutes, regulations, precedents referenced\n"
            "- **Arguments Made** — by each party/speaker\n"
            "- **Obligations & Deadlines** — filing dates, compliance requirements\n"
            "- **Risk Factors** — potential liabilities or exposures identified\n"
            "Use legal terminology precisely.\n\n"
        ),
        "livestream_notes_prefix": "",
    },
    "Education": {
        "model": None,  # Placeholder — configure via EDUCATION_AI_MODEL env var when ready
        "summary_prefix": (
            "You are an educational content specialist. Structure the summary as:\n"
            "- **Learning Objectives** covered in the session\n"
            "- **Key Concepts & Definitions** introduced\n"
            "- **Examples & Illustrations** used\n"
            "- **Assignments & Assessments** mentioned\n"
            "- **Study Resources** referenced\n"
            "Format for student comprehension and review.\n\n"
        ),
        "insights_prefix": (
            "You are an educational analyst. Extract:\n"
            "- **Core Topics** — main subjects and subtopics covered\n"
            "- **Key Takeaways** — critical concepts students should remember\n"
            "- **Action Items** — homework, readings, project deadlines\n"
            "- **Questions Raised** — unanswered student questions for follow-up\n"
            "Format for easy study reference.\n\n"
        ),
        "livestream_notes_prefix": "",
    },
    "Finance": {
        "model": None,  # Placeholder — configure via FINANCE_AI_MODEL env var when ready
        "summary_prefix": (
            "You are a financial transcription specialist. Structure the summary highlighting:\n"
            "- **Financial Metrics & KPIs** discussed\n"
            "- **Budget Items & Allocations** mentioned\n"
            "- **Investment Decisions & Risk Assessments**\n"
            "- **Regulatory & Compliance Matters**\n"
            "- **Action Items with Financial Impact**\n"
            "Use precise financial terminology. Note any figures, percentages, and currency amounts exactly.\n\n"
        ),
        "insights_prefix": (
            "You are a financial analyst. Extract:\n"
            "- **Key Financial Data** — numbers, projections, comparisons mentioned\n"
            "- **Decisions with Financial Impact** — approvals, budget changes\n"
            "- **Risk Factors** — identified financial risks or exposures\n"
            "- **Compliance Items** — regulatory requirements discussed\n"
            "- **Follow-up Actions** — with estimated financial impact where possible\n"
            "Be precise with all numerical values.\n\n"
        ),
        "livestream_notes_prefix": "",
    },
}

# Security
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(170 * 1024 * 1024)))  # 170 MB
MAX_LIVESTREAM_SECONDS = int(os.getenv("MAX_LIVESTREAM_SECONDS", "7200"))  # 2 hours
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

# AssemblyAI client
assemblyai_available = False
if ASSEMBLYAI_API_KEY:
    try:
        import assemblyai as aai
        aai.settings.api_key = ASSEMBLYAI_API_KEY
        aai.settings.http_timeout = 1800.0  # Allow large file uploads to take up to 30 mins
        assemblyai_available = True
        logger.info("AssemblyAI client initialised.")
    except ImportError:
        logger.warning("AssemblyAI SDK not installed — pip install assemblyai")

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


async def _call_ai_with_model(system_prompt: str, user_prompt: str, model_override: str = None) -> str:
    """Run AI chat completion with optional model override for subject-matter routing.

    If model_override is provided and non-empty, attempts to use it.
    Falls back to the default model if the override fails.
    """
    model = model_override or _hf_active_model
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await asyncio.to_thread(
            hf_client.chat_completion,
            model=model,
            messages=messages,
            max_tokens=2048,
            temperature=0.3,
        )
        return response.choices[0].message.content
    except Exception as exc:
        if model != _hf_active_model:
            logger.warning(
                "Subject-matter model '%s' failed (%s), falling back to default model.",
                model, exc,
            )
            response = await asyncio.to_thread(
                hf_client.chat_completion,
                model=_hf_active_model,
                messages=messages,
                max_tokens=2048,
                temperature=0.3,
            )
            return response.choices[0].message.content
        raise


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
# Support Document RAG — text extraction, chunking, embedding, retrieval
# ---------------------------------------------------------------------------
DOC_CHUNK_WORD_LIMIT = int(os.getenv("DOC_CHUNK_WORD_LIMIT", "250"))


def _extract_text_from_document(filename: str, content_type: Optional[str], file_bytes: bytes) -> str:
    """Extract plain text from an uploaded support document."""
    lower_name = (filename or "").lower()

    if lower_name.endswith(".pdf") or content_type == "application/pdf":
        reader = PdfReader(io.BytesIO(file_bytes))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)

    if lower_name.endswith(".docx") or content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        doc = DocxDocument(io.BytesIO(file_bytes))
        return "\n\n".join(p.text for p in doc.paragraphs)

    # txt, md, csv, and anything else — treat as plain text
    return file_bytes.decode("utf-8", errors="replace")


def _embed_text(text: str) -> list[float]:
    """Generate a single dense embedding vector for a piece of text via HF Inference."""
    result = hf_client.feature_extraction(text, model=EMBEDDING_MODEL)
    arr = np.array(result, dtype=float)
    if arr.ndim > 1:
        # Some models return per-token vectors — mean-pool into one sentence vector.
        arr = arr.mean(axis=0)
    return arr.flatten().tolist()


async def _retrieve_relevant_chunks(user_id: str, query_text: str, top_k: int = 5) -> list[dict]:
    """Vector-search a user's uploaded support documents for chunks relevant to query_text."""
    if not supabase or not query_text.strip():
        return []
    try:
        query_embedding = await asyncio.to_thread(_embed_text, query_text)
        resp = await asyncio.to_thread(
            lambda: supabase.rpc(
                "match_document_chunks",
                {
                    "query_embedding": query_embedding,
                    "match_user_id": user_id,
                    "match_count": top_k,
                },
            ).execute()
        )
        return resp.data or []
    except Exception as exc:
        logger.warning("Document retrieval failed for user %s: %s", user_id, exc)
        return []


@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...), user=Depends(get_current_user)):
    """Upload a support document: store it, extract its text, chunk it, and embed
    each chunk for later RAG retrieval during fact-checking / cross-referencing."""
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required.")
    if not supabase:
        raise HTTPException(status_code=503, detail="Storage service unavailable.")

    file_bytes = await file.read()
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
        )

    file_name = file.filename or "document"
    storage_path = f"{user.id}/{uuid.uuid4()}-{file_name}"

    try:
        await asyncio.to_thread(
            supabase.storage.from_("context_documents").upload,
            storage_path,
            file_bytes,
            {"content-type": file.content_type or "application/octet-stream"},
        )

        doc_resp = await asyncio.to_thread(
            lambda: _execute_with_retry(
                supabase.table("context_documents").insert({
                    "user_id": user.id,
                    "file_name": file_name,
                    "storage_path": storage_path,
                    "content_type": file.content_type,
                    "size_bytes": len(file_bytes),
                })
            )
        )
        document_id = doc_resp.data[0]["id"]

        text = _extract_text_from_document(file_name, file.content_type, file_bytes)
        chunks = _split_transcript_into_chunks(text, max_words=DOC_CHUNK_WORD_LIMIT) if text.strip() else []

        chunk_rows = []
        for i, chunk_text in enumerate(chunks):
            if not chunk_text.strip():
                continue
            embedding = await asyncio.to_thread(_embed_text, chunk_text)
            chunk_rows.append({
                "document_id": document_id,
                "user_id": user.id,
                "chunk_index": i,
                "chunk_text": chunk_text,
                "embedding": embedding,
            })

        if chunk_rows:
            await asyncio.to_thread(
                lambda: _execute_with_retry(
                    supabase.table("document_chunks").insert(chunk_rows)
                )
            )

        return {
            "id": document_id,
            "file_name": file_name,
            "chunks_indexed": len(chunk_rows),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Document upload failed")
        raise HTTPException(status_code=500, detail=f"Document upload failed: {exc}")


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class AIFeaturesRequest(BaseModel):
    transcript: str
    feature_type: str  # "summary" | "insights" | "translation"
    metadata: Optional[dict] = None
    target_language: Optional[str] = "French"
    subject_matter: Optional[str] = "General"

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


# ---------------------------------------------------------------------------
# Auth — registration
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: str
    password: str
    first_name: str
    last_name: str
    organization_name: str
    organization_address: Optional[str] = None


@app.post("/api/auth/register")
async def register_user(payload: RegisterRequest):
    """Create a new account.

    Gated by REQUIRE_EMAIL_CONFIRMATION:
    - false (testing/dev, current default): account is created pre-confirmed via the
      admin API and immediately signed in, so the frontend can redirect straight to
      the dashboard with no email step.
    - true (flip before production launch): account is created unconfirmed and Supabase
      sends its normal confirmation email; no session is returned.
    """
    if not supabase:
        raise HTTPException(status_code=503, detail="Auth service unavailable.")

    user_metadata = {
        "first_name": payload.first_name,
        "last_name": payload.last_name,
        "organization_name": payload.organization_name,
        "organization_address": payload.organization_address,
    }

    try:
        create_resp = await asyncio.to_thread(
            supabase.auth.admin.create_user,
            {
                "email": payload.email,
                "password": payload.password,
                "email_confirm": not REQUIRE_EMAIL_CONFIRMATION,
                "user_metadata": user_metadata,
            },
        )
    except Exception as exc:
        logger.warning("Registration failed for %s: %s", payload.email, exc)
        raise HTTPException(status_code=400, detail=str(exc))

    if not create_resp or not create_resp.user:
        raise HTTPException(status_code=400, detail="Registration failed.")

    if REQUIRE_EMAIL_CONFIRMATION:
        return {"status": "pending_confirmation"}

    try:
        session_resp = await asyncio.to_thread(
            supabase.auth.sign_in_with_password,
            {"email": payload.email, "password": payload.password},
        )
    except Exception as exc:
        logger.error("Post-registration auto sign-in failed for %s: %s", payload.email, exc)
        # Account was created successfully even though auto sign-in failed — let the
        # user sign in manually rather than surfacing this as a registration failure.
        return {"status": "pending_confirmation"}

    return {
        "status": "confirmed",
        "access_token": session_resp.session.access_token,
        "refresh_token": session_resp.session.refresh_token,
    }


@app.get("/api/dashboard/stats")
async def get_dashboard_stats(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required.")
    if not supabase:
        return {"files_processed": 0, "total_seconds": 0}

    try:
        resp = await asyncio.to_thread(
            lambda: _execute_with_retry(
                supabase.table("usage_logs")
                .select("duration_seconds")
                .eq("user_id", user.id)
            )
        )
        files_processed = len(resp.data)
        total_seconds = sum(r["duration_seconds"] for r in resp.data)
    except Exception as exc:
        logger.warning("Failed to fetch dashboard stats for %s: %s", user.id, exc)
        files_processed, total_seconds = 0, 0

    return {
        "files_processed": files_processed,
        "total_seconds": total_seconds,
        "total_minutes": round(total_seconds / 60.0, 1),
        "total_hours": round(total_seconds / 3600.0, 2),
    }


# ---------------------------------------------------------------------------
# Transcription provider helpers
# ---------------------------------------------------------------------------
async def _transcribe_with_deepgram(file_bytes: bytes, filename: str) -> dict:
    """Transcribe audio using Deepgram (fast, best for shorter files)."""
    if not deepgram_client:
        raise HTTPException(status_code=500, detail="Deepgram transcription service not configured.")

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

    return {
        "duration_seconds": duration_seconds,
        "paragraphs": paragraphs or [{"speaker": "Speaker 0", "text": transcript_text}],
        "raw_transcript": transcript_text or " ".join(p["text"] for p in paragraphs),
        "provider": "deepgram",
    }


async def _transcribe_with_assemblyai(file_bytes: bytes, filename: str) -> dict:
    """Transcribe audio using AssemblyAI (handles long-form audio natively via async processing)."""
    if not assemblyai_available:
        raise HTTPException(status_code=500, detail="AssemblyAI transcription service not configured.")

    import assemblyai as aai
    import requests

    logger.info("AssemblyAI: uploading %s (%d bytes) manually via requests...", filename, len(file_bytes))

    # 1. Upload the file manually using requests to bypass httpx large-file timeouts
    headers = {'authorization': aai.settings.api_key}
    
    def _compress_audio(raw_bytes: bytes) -> bytes:
        try:
            import av
            import io
            logger.info("AssemblyAI: Compressing large audio file to 32kbps mono mp3...")
            in_buffer = io.BytesIO(raw_bytes)
            input_container = av.open(in_buffer)
            
            in_stream = None
            for s in input_container.streams:
                if s.type == 'audio':
                    in_stream = s
                    break
            if not in_stream:
                return raw_bytes
                
            out_buffer = io.BytesIO()
            output_container = av.open(out_buffer, mode="w", format="mp3")
            out_stream = output_container.add_stream("libmp3lame", rate=16000)
            out_stream.bit_rate = 32000
            out_stream.layout = "mono"
            
            resampler = av.AudioResampler(
                format="s16p", 
                layout="mono", 
                rate=16000
            )
            
            for frame in input_container.decode(in_stream):
                for rframe in resampler.resample(frame):
                    for packet in out_stream.encode(rframe):
                        output_container.mux(packet)
                        
            for frame in resampler.resample(None):
                for packet in out_stream.encode(frame):
                    output_container.mux(packet)
                    
            for packet in out_stream.encode(None):
                output_container.mux(packet)
                
            output_container.close()
            compressed = out_buffer.getvalue()
            logger.info("AssemblyAI: Compression complete. Size reduced from %d to %d bytes", len(raw_bytes), len(compressed))
            return compressed
        except Exception as e:
            logger.warning("AssemblyAI: Audio compression skipped due to error: %s", str(e))
            return raw_bytes

    def upload_file():
        import time
        
        # Compress if file is > 20MB
        payload_bytes = file_bytes
        if len(payload_bytes) > 20 * 1024 * 1024:
            payload_bytes = _compress_audio(payload_bytes)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info("AssemblyAI: upload attempt %d/%d (direct payload)...", attempt + 1, max_retries)
                response = requests.post(
                    'https://api.assemblyai.com/v2/upload',
                    headers=headers,
                    data=payload_bytes,  # Pass raw bytes directly; avoids chunking overhead
                    timeout=3600
                )
                response.raise_for_status()
                return response.json()['upload_url']
            except Exception as e:
                logger.error("AssemblyAI upload attempt %d failed: %s", attempt + 1, str(e))
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)

    try:
        upload_url = await asyncio.to_thread(upload_file)
        logger.info("AssemblyAI: upload complete. Submitting transcription job...")
    except Exception as e:
        raise Exception(f"AssemblyAI upload error: {str(e)}")

    # 2. Submit the transcription job using the upload URL
    config = aai.TranscriptionConfig(
        speaker_labels=True,
        punctuate=True,
        format_text=True,
    )

    transcriber = aai.Transcriber()

    # This submits the job and polls until complete
    transcript = await asyncio.to_thread(
        transcriber.transcribe, upload_url, config=config
    )

    if transcript.status == aai.TranscriptStatus.error:
        raise Exception(f"AssemblyAI error: {transcript.error}")

    duration_seconds = round((transcript.audio_duration or 0))

    # Parse utterances into the standard paragraph format
    paragraphs: list[dict] = []
    if transcript.utterances:
        for utt in transcript.utterances:
            paragraphs.append({
                "speaker": f"Speaker {utt.speaker}",
                "text": utt.text,
            })

    raw_transcript = transcript.text or ""

    return {
        "duration_seconds": duration_seconds,
        "paragraphs": paragraphs or [{"speaker": "Speaker A", "text": raw_transcript}],
        "raw_transcript": raw_transcript,
        "provider": "assemblyai",
    }


def _split_audio_into_chunks(file_bytes: bytes, chunk_minutes: int = AUDIO_CHUNK_MINUTES) -> list[tuple[bytes, float]]:
    """Split audio bytes into time-based chunks using PyAV.

    Returns a list of (chunk_bytes_as_mp3, start_offset_seconds) tuples.
    Falls back to returning the original file as a single chunk if anything goes wrong.
    """
    try:
        import av
        import io as _io

        chunk_duration = chunk_minutes * 60.0

        # Probe the total duration first (read-only pass)
        with av.open(_io.BytesIO(file_bytes)) as probe:
            audio_stream = next((s for s in probe.streams if s.type == "audio"), None)
            if not audio_stream:
                logger.warning("Audio chunking: no audio stream found — returning original file")
                return [(file_bytes, 0.0)]
            total_seconds = (
                float(audio_stream.duration * audio_stream.time_base)
                if audio_stream.duration and audio_stream.time_base
                else 0.0
            )

        if total_seconds <= 0 or total_seconds <= chunk_duration:
            return [(file_bytes, 0.0)]

        n_chunks = int(total_seconds / chunk_duration) + (1 if total_seconds % chunk_duration else 0)
        logger.info(
            "Audio chunking: %.1f s → %d chunks of %d min each",
            total_seconds, n_chunks, chunk_minutes,
        )

        chunks: list[tuple[bytes, float]] = []
        for i in range(n_chunks):
            start = i * chunk_duration
            end = min(start + chunk_duration, total_seconds)

            in_container = av.open(_io.BytesIO(file_bytes))
            in_stream = next(s for s in in_container.streams if s.type == "audio")

            out_buf = _io.BytesIO()
            out_container = av.open(out_buf, mode="w", format="mp3")
            out_stream = out_container.add_stream("libmp3lame", rate=16000)
            out_stream.bit_rate = 64000
            out_stream.layout = "mono"

            resampler = av.AudioResampler(format="s16p", layout="mono", rate=16000)

            # Seek to chunk start
            seek_ts = int(start / float(in_stream.time_base))
            in_container.seek(seek_ts, stream=in_stream)

            for frame in in_container.decode(in_stream):
                frame_time = float(frame.pts * in_stream.time_base) if frame.pts is not None else start
                if frame_time >= end:
                    break
                for rframe in resampler.resample(frame):
                    for packet in out_stream.encode(rframe):
                        out_container.mux(packet)

            # Flush resampler + encoder
            for rframe in resampler.resample(None):
                for packet in out_stream.encode(rframe):
                    out_container.mux(packet)
            for packet in out_stream.encode(None):
                out_container.mux(packet)

            out_container.close()
            in_container.close()

            chunk_data = out_buf.getvalue()
            if chunk_data:
                chunks.append((chunk_data, start))
            else:
                logger.warning("Audio chunking: chunk %d/%d produced empty bytes — skipping", i + 1, n_chunks)

        return chunks if chunks else [(file_bytes, 0.0)]

    except Exception as exc:
        logger.warning("Audio chunking failed (%s) — falling back to single-file transcription", exc)
        return [(file_bytes, 0.0)]


async def _transcribe_with_deepgram_chunked(file_bytes: bytes, filename: str) -> dict:
    """Transcribe audio using Deepgram with parallel chunk processing.

    - For files short enough to fit in one chunk, delegates directly to _transcribe_with_deepgram.
    - For longer files, splits into AUDIO_CHUNK_MINUTES-length segments, transcribes all in
      parallel via asyncio.gather, then stitches the results together.
    - Speaker labels are kept chunk-local (Deepgram cannot cross-match speakers across requests),
      so labels within each chunk are reliable but may restart numbering per chunk.
    """
    chunks = await asyncio.to_thread(_split_audio_into_chunks, file_bytes)

    if len(chunks) == 1:
        # Single chunk — use the standard path (no overhead)
        return await _transcribe_with_deepgram(file_bytes, filename)

    logger.info("Parallel Deepgram transcription: %d chunks", len(chunks))

    async def _transcribe_chunk(chunk_bytes: bytes, offset: float, idx: int) -> dict:
        chunk_filename = f"chunk_{idx:03d}_{filename}"
        result = await _transcribe_with_deepgram(chunk_bytes, chunk_filename)
        return result

    tasks = [_transcribe_chunk(cb, off, i) for i, (cb, off) in enumerate(chunks)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_paragraphs: list[dict] = []
    raw_parts: list[str] = []
    total_duration = 0

    for i, res in enumerate(results):
        if isinstance(res, Exception):
            logger.error("Chunk %d transcription failed: %s", i, res)
            continue
        total_duration += res.get("duration_seconds", 0)
        raw_parts.append(res.get("raw_transcript", ""))
        all_paragraphs.extend(res.get("paragraphs", []))

    if not all_paragraphs and not raw_parts:
        raise RuntimeError("All audio chunks failed during transcription")

    return {
        "duration_seconds": total_duration,
        "paragraphs": all_paragraphs,
        "raw_transcript": "\n\n".join(p for p in raw_parts if p),
        "provider": "deepgram-chunked",
    }


def _select_transcription_provider(file_size_bytes: int) -> str:
    """Determine which transcription provider to use based on config and file size.

    Routing logic:
    - Explicit override (TRANSCRIPTION_PROVIDER env var) → honour it.
    - Files above ASSEMBLYAI_FALLBACK_BYTES → AssemblyAI directly (avoids chunking overhead
      for very large files that AssemblyAI handles natively).
    - Everything else → Deepgram with parallel chunking.
    """
    if TRANSCRIPTION_PROVIDER == "assemblyai":
        if not assemblyai_available:
            logger.warning("AssemblyAI requested but not configured — falling back to Deepgram.")
            return "deepgram"
        return "assemblyai"
    elif TRANSCRIPTION_PROVIDER == "deepgram":
        return "deepgram"
    else:  # "auto"
        if assemblyai_available and file_size_bytes > ASSEMBLYAI_FALLBACK_BYTES:
            return "assemblyai"
        return "deepgram"


@app.post("/api/transcribe")
async def transcribe_audio(
    request: Request,
    file: Optional[UploadFile] = File(None),
    file_path: Optional[str] = Form(None),
    user_hash: str = Depends(get_user_hash),
    user=Depends(get_current_user),
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
        if file_path:
            if not supabase:
                raise HTTPException(status_code=503, detail="Storage service unavailable.")
            try:
                storage_res = await asyncio.to_thread(
                    supabase.storage.from_("context_documents").download,
                    file_path
                )
                file_bytes = storage_res
                filename = os.path.basename(file_path)
            except Exception as exc:
                logger.error("Failed to download audio from storage: %s", exc)
                raise HTTPException(status_code=400, detail="Failed to retrieve uploaded audio from storage.")
            
            # Use fixed 170MB limit for storage uploads bypassing Nginx
            if len(file_bytes) > (170 * 1024 * 1024):
                raise HTTPException(
                    status_code=413,
                    detail="File too large. Maximum allowed size is 170 MB.",
                )
        elif file:
            file_bytes = await file.read()
            filename = file.filename or "audio.webm"
            if len(file_bytes) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Maximum allowed size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
                )
        else:
            raise HTTPException(status_code=400, detail="Must provide either file or file_path")

        provider = _select_transcription_provider(len(file_bytes))
        logger.info(
            "Transcribe: file=%s size=%d bytes provider=%s",
            filename,
            len(file_bytes),
            provider,
        )

        # Route to the selected provider
        if provider == "assemblyai":
            result = await _transcribe_with_assemblyai(file_bytes, filename)
        else:
            # Deepgram with parallel chunking; fall back to AssemblyAI on failure
            try:
                result = await _transcribe_with_deepgram_chunked(file_bytes, filename)
            except Exception as deepgram_exc:
                if assemblyai_available:
                    logger.warning(
                        "Deepgram transcription failed (%s) — falling back to AssemblyAI.", deepgram_exc
                    )
                    result = await _transcribe_with_assemblyai(file_bytes, filename)
                else:
                    raise


        # Cleanup temp audio from Supabase
        if file_path:
            try:
                await asyncio.to_thread(
                    supabase.storage.from_("context_documents").remove,
                    [file_path]
                )
            except Exception as exc:
                logger.warning("Failed to clean up temp audio file %s: %s", file_path, exc)

        duration_seconds = result["duration_seconds"]

        if duration_seconds > MAX_RECORDING_DURATION_MINUTES * 60:
            raise HTTPException(
                status_code=400,
                detail=f"Audio exceeds {MAX_RECORDING_DURATION_MINUTES}-minute limit.",
            )

        # Log usage
        if supabase:
            try:
                usage_row = {"user_hash": user_hash, "duration_seconds": duration_seconds}
                if user:
                    usage_row["user_id"] = user.id
                await asyncio.to_thread(
                    lambda: _execute_with_retry(
                        supabase.table("usage_logs")
                        .insert(usage_row)
                    )
                )
            except Exception as db_err:
                logger.error("Failed to log usage: %s", db_err)

        logger.info("Transcription complete via %s: %d seconds of audio", provider, duration_seconds)
        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Transcription error")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}")


@app.post("/api/ai-features")
async def generate_ai_features(payload: AIFeaturesRequest):
    if not payload.transcript.strip():
        raise HTTPException(status_code=400, detail="Transcript is empty.")

    # Resolve subject matter configuration
    sm_key = payload.subject_matter or "General"
    sm_config = SUBJECT_MATTER_CONFIG.get(sm_key, SUBJECT_MATTER_CONFIG["General"])
    sm_model = sm_config.get("model")  # May be None — falls back to default

    # Build prompt
    user_prompt = f"Transcript:\n{payload.transcript}\n\n"

    if payload.feature_type == "summary":
        domain_prefix = sm_config.get("summary_prefix", "")
        system_prompt = domain_prefix + (
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
        domain_prefix = sm_config.get("insights_prefix", "")
        system_prompt = domain_prefix + (
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
        result_text = await _call_ai_with_model(system_prompt, user_prompt, model_override=sm_model)
        return {"result": result_text, "subject_matter": sm_key}
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
    subject_matter: Optional[str] = "General"


@app.post("/api/livestream/ai-notes")
async def generate_livestream_notes(payload: LivestreamAINotesRequest):
    """Generate AI-powered notes, action items, and key decisions from transcript."""
    if not payload.transcript.strip():
        raise HTTPException(status_code=400, detail="Transcript is empty.")

    # Resolve subject matter configuration
    sm_key = payload.subject_matter or "General"
    sm_config = SUBJECT_MATTER_CONFIG.get(sm_key, SUBJECT_MATTER_CONFIG["General"])
    sm_model = sm_config.get("model")
    domain_prefix = sm_config.get("livestream_notes_prefix", "")

    system_prompt = domain_prefix + (
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
        return {"result": result_text, "subject_matter": sm_key}
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


async def _fact_check_single_claim(claim_obj, doc_chunks: Optional[list] = None) -> dict:
    """Verify one claim: search the web (Serper) for evidence, retrieve relevant
    support-document chunks (if any), then ask the LLM for a verdict. Shared by
    the livestream fact-check endpoint and the Synthesis Engine cross-reference endpoint.
    """
    claim_text = claim_obj.get("claim", "") if isinstance(claim_obj, dict) else str(claim_obj)
    speaker = claim_obj.get("speaker", "") if isinstance(claim_obj, dict) else ""
    category = claim_obj.get("category", "") if isinstance(claim_obj, dict) else ""

    # Step 1: Search the web for evidence
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

    # Retrieved (RAG) document evidence, scoped to this specific claim
    doc_context = ""
    doc_sources = []
    if doc_chunks:
        doc_context = "SUPPORT DOCUMENT EVIDENCE:\n\n"
        for chunk in doc_chunks:
            doc_context += f"--- {chunk.get('file_name', 'document')} ---\n{chunk.get('chunk_text', '')}\n\n"
            doc_sources.append({
                "file_name": chunk.get("file_name", "document"),
                "snippet": chunk.get("chunk_text", "")[:300],
                "similarity": chunk.get("similarity"),
            })

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

    if doc_context:
        eval_prompt += f"Internal Organization Evidence:\n{doc_context}\n"

    if not evidence and not doc_context:
        eval_prompt += "No external evidence available. Use your knowledge base only.\n"

    try:
        eval_result = await _call_ai(eval_system, eval_prompt)
        eval_result = eval_result.strip()
        if eval_result.startswith("```"):
            eval_result = eval_result.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        verdict_data = json.loads(eval_result)
        return {
            "claim": claim_text,
            "speaker": speaker,
            "category": category,
            "verdict": verdict_data.get("verdict", "UNVERIFIABLE"),
            "confidence": verdict_data.get("confidence", 0.5),
            "explanation": verdict_data.get("explanation", ""),
            "key_evidence": verdict_data.get("key_evidence", ""),
            "sources": sources,
            "document_sources": doc_sources,
            "used_web_search": bool(evidence),
        }
    except (json.JSONDecodeError, Exception) as eval_err:
        logger.warning("Fact-check evaluation failed for claim '%s': %s", claim_text[:50], eval_err)
        return {
            "claim": claim_text,
            "speaker": speaker,
            "category": category,
            "verdict": "UNVERIFIABLE",
            "confidence": 0.0,
            "explanation": "Evaluation failed.",
            "sources": sources,
            "document_sources": doc_sources,
            "used_web_search": bool(evidence),
        }


@app.post("/api/livestream/fact-check")
async def fact_check_claims(payload: FactCheckRequest, user=Depends(get_current_user)):
    """Verify factual claims using web search (Serper) + support-document RAG + AI evaluation.

    Pipeline per claim:
    1. Search the web for evidence (via Serper API)
    2. Vector-retrieve the most relevant chunks from the user's uploaded support documents
    3. Feed claim + web evidence + retrieved document chunks to AI for evaluation
    4. Return verdict: TRUE, FALSE, MISLEADING, UNVERIFIABLE with confidence + sources

    Falls back to LLM-only evaluation if Serper API and support documents are both unavailable.
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

    use_docs = bool(payload.use_org_docs and user)

    results = []
    for claim_obj in claims_to_check:
        claim_text = claim_obj.get("claim", "") if isinstance(claim_obj, dict) else str(claim_obj)
        if not claim_text.strip():
            continue

        doc_chunks = await _retrieve_relevant_chunks(user.id, claim_text) if use_docs else []
        results.append(await _fact_check_single_claim(claim_obj, doc_chunks))

    return {"results": results}


# ---------------------------------------------------------------------------
# Synthesis Engine — Cross-Reference & Fact-Check (logged-in users only)
# ---------------------------------------------------------------------------
class SynthesisCrossReferenceRequest(BaseModel):
    transcript: str
    context: Optional[dict] = None


@app.post("/api/synthesis/cross-reference")
async def synthesis_cross_reference(payload: SynthesisCrossReferenceRequest, user=Depends(get_current_user)):
    """Cross-reference a Synthesis Engine transcript against the logged-in user's uploaded
    support documents (vector RAG) and the web (Serper), claim by claim.

    Reuses the same claim-detection and fact-check pipeline as livestream fact-checking —
    just applied to a transcript the user already produced via /api/transcribe.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required to use cross-referencing.")
    if not payload.transcript.strip():
        raise HTTPException(status_code=400, detail="Transcript is empty.")

    detect_result = await detect_claims(ClaimDetectionRequest(
        transcript=payload.transcript,
        context=payload.context,
    ))
    claims_to_check = detect_result.get("claims", [])

    if not claims_to_check:
        return {"results": [], "message": "No verifiable claims detected."}

    results = []
    for claim_obj in claims_to_check:
        claim_text = claim_obj.get("claim", "") if isinstance(claim_obj, dict) else str(claim_obj)
        if not claim_text.strip():
            continue
        doc_chunks = await _retrieve_relevant_chunks(user.id, claim_text)
        results.append(await _fact_check_single_claim(claim_obj, doc_chunks))

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

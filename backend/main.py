import os
import hashlib
import tempfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client
from deepgram import DeepgramClient, PrerecordedOptions, FileSource
from huggingface_hub import InferenceClient
import edge_tts
import httpx

# Load environment variables
load_dotenv()

# App configuration
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
HF_TOKEN = os.getenv("HF_TOKEN")

MONTHLY_LIMIT_MINUTES = int(os.getenv("MONTHLY_LIMIT_MINUTES", "3500"))
USER_MONTHLY_LIMIT_MINUTES = int(os.getenv("USER_MONTHLY_LIMIT_MINUTES", "60"))
MAX_RECORDING_DURATION_MINUTES = int(os.getenv("MAX_RECORDING_DURATION_MINUTES", "30"))

# Initialize clients
supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"Failed to initialize Supabase client: {e}")

deepgram_client = DeepgramClient(DEEPGRAM_API_KEY) if DEEPGRAM_API_KEY else None

hf_client = InferenceClient(token=HF_TOKEN) if HF_TOKEN else InferenceClient()

app = FastAPI(
    title="relaxntakenotes.africa API",
    description="Speech-to-Text and AI Note-Taking backend platform.",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production if necessary
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Helper function to get real client IP
def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"

# Helper function to compute user hash
def get_user_hash(request: Request, x_user_uuid: Optional[str] = Header(None)) -> str:
    client_ip = get_client_ip(request)
    uuid_part = x_user_uuid or "anonymous"
    hash_input = f"{client_ip}-{uuid_part}"
    return hashlib.sha256(hash_input.encode()).hexdigest()

# Helper function to get start of current month in UTC
def get_start_of_month() -> str:
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat()

# Fetch usage logs from Supabase
def get_usage_stats(user_hash: str):
    if not supabase:
        # Mock database stats if Supabase is not configured yet
        return 0, 0
        
    start_time = get_start_of_month()
    
    try:
        # Get user's monthly usage
        user_response = supabase.table("usage_logs") \
            .select("duration_seconds") \
            .eq("user_hash", user_hash) \
            .gte("created_at", start_time) \
            .execute()
        user_seconds = sum(item["duration_seconds"] for item in user_response.data)
        
        # Get global monthly usage
        global_response = supabase.table("usage_logs") \
            .select("duration_seconds") \
            .gte("created_at", start_time) \
            .execute()
        global_seconds = sum(item["duration_seconds"] for item in global_response.data)
        
        return user_seconds, global_seconds
    except Exception as e:
        print(f"Database error fetching usage stats: {e}")
        # Return 0 if query fails (graceful degradation, or we can block)
        return 0, 0

# Pydantic schemas for request validation
class AIFeaturesRequest(BaseModel):
    transcript: str
    feature_type: str  # "summary", "insights", "translation"
    metadata: Optional[dict] = None
    target_language: Optional[str] = "French"

class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = "en-US-JennyNeural"

@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "relaxntakenotes.africa API",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/api/status")
async def get_status(request: Request, user_hash: str = Depends(get_user_hash)):
    user_seconds, global_seconds = get_usage_stats(user_hash)
    
    user_minutes = user_seconds / 60.0
    global_minutes = global_seconds / 60.0
    
    return {
        "global_usage_minutes": round(global_minutes, 2),
        "global_limit_minutes": MONTHLY_LIMIT_MINUTES,
        "user_usage_minutes": round(user_minutes, 2),
        "user_limit_minutes": USER_MONTHLY_LIMIT_MINUTES,
        "is_over_budget": global_minutes >= MONTHLY_LIMIT_MINUTES,
        "user_is_over_limit": user_minutes >= USER_MONTHLY_LIMIT_MINUTES,
        "max_recording_duration_minutes": MAX_RECORDING_DURATION_MINUTES
    }

@app.post("/api/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    user_hash: str = Depends(get_user_hash)
):
    # Detect if we are in mock mode due to placeholder credentials
    is_mock_mode = (
        not DEEPGRAM_API_KEY 
        or DEEPGRAM_API_KEY == "your_deepgram_api_key_here"
        or "fake" in DEEPGRAM_API_KEY.lower()
    )
    
    # Check limits if Supabase is configured
    if supabase:
        user_seconds, global_seconds = get_usage_stats(user_hash)
        
        if (global_seconds / 60.0) >= MONTHLY_LIMIT_MINUTES:
            raise HTTPException(status_code=403, detail="Global platform transcription budget has been exceeded for this month. Please try again next month.")
            
        if (user_seconds / 60.0) >= USER_MONTHLY_LIMIT_MINUTES:
            raise HTTPException(status_code=403, detail="You have reached your personal monthly transcription limit. Upgrade to a paid plan for unlimited hours.")
    else:
        user_seconds, global_seconds = 0, 0

    try:
        # Read the file content
        file_bytes = await file.read()
        
        if is_mock_mode:
            # Realistic 5-minute pre-screening interview diarized mock transcript
            duration_seconds = 300  # 5 minutes
            
            paragraphs = [
                {
                    "speaker": "Speaker 0",
                    "text": "Hi Sarah, thanks for joining the sync. Let's discuss the hiring pipeline and what systems our HR department is using to pre-screen candidates' CVs."
                },
                {
                    "speaker": "Speaker 1",
                    "text": "Hi John. Currently, we utilize Greenhouse as our main Applicant Tracking System, or ATS. When candidates upload their resumes, the system scans them for key skills like Python, React, and system architecture."
                },
                {
                    "speaker": "Speaker 0",
                    "text": "Got it. But I'm concerned about keyword filtering bias. Some candidates might be highly skilled but didn't write the exact keywords. How do we address that?"
                },
                {
                    "speaker": "Speaker 1",
                    "text": "That is a valid concern. To prevent that, we have set up the ATS to only screen out applicants who miss fundamental requirements, like years of experience. For the rest, we do a manual screening pass. It takes our team about 5 minutes per CV to do a quick portfolio check."
                },
                {
                    "speaker": "Speaker 0",
                    "text": "Good. Now, during the initial verbal phone screening, who is taking notes? It seems we spend too much time writing summary reports after each call."
                },
                {
                    "speaker": "Speaker 1",
                    "text": "Right now, the recruiters take notes manually while talking. It is quite distracting, and they often miss important details. If we use Deepgram's speaker diarization, we could automatically transcribe the calls and map who said what."
                },
                {
                    "speaker": "Speaker 0",
                    "text": "Exactly. Let's test the 'Relax n Take Notes' platform today with a sample call to see if it maps participants correctly and extracts clear takeaways for our HR files. If this works, we'll roll it out to the whole team next week."
                },
                {
                    "speaker": "Speaker 1",
                    "text": "Perfect, let's start the test run now and see how the AI note-taking and summarization performs."
                }
            ]
            
            return {
                "duration_seconds": duration_seconds,
                "paragraphs": paragraphs,
                "raw_transcript": " ".join(p["text"] for p in paragraphs)
            }
            
        # Real transcription using Deepgram
        if not deepgram_client:
            raise HTTPException(status_code=500, detail="Deepgram client is not initialized.")
            
        # Deepgram Prerecorded transcription options
        options = PrerecordedOptions(
            model="nova-2",
            smart_format=True,
            diarize=True,
            punctuate=True,
        )
        
        # Send audio payload to Deepgram
        payload = {"buffer": file_bytes, "mimetype": file.content_type}
        response = deepgram_client.listen.prerecorded.v("1").transcribe_file(
            payload, 
            options, 
            timeout=httpx.Timeout(300.0, connect=30.0)
        )
        
        # Convert response object to dict for safe retrieval
        response_dict = response.to_dict()
        
        # Parse output
        metadata = response_dict.get("metadata", {})
        duration_seconds = round(metadata.get("duration", 0))
        
        # Validate that the duration does not exceed the limit
        if duration_seconds > MAX_RECORDING_DURATION_MINUTES * 60:
            raise HTTPException(status_code=400, detail=f"Audio duration exceeds the maximum allowed limit of {MAX_RECORDING_DURATION_MINUTES} minutes.")
            
        # Extract transcript with speaker info if available
        results = response_dict.get("results", {})
        channels = results.get("channels", [])
        
        transcript_text = ""
        paragraphs = []
        
        if channels:
            alternatives = channels[0].get("alternatives", [])
            if alternatives:
                words = alternatives[0].get("words", [])
                paragraphs_data = alternatives[0].get("paragraphs", {}).get("paragraphs", [])
                
                if paragraphs_data:
                    # Diarized paragraph formatting
                    for p in paragraphs_data:
                        speaker = p.get("speaker", 0)
                        sentences = []
                        for s in p.get("sentences", []):
                            sentences.append(s.get("text", ""))
                        paragraph_text = " ".join(sentences)
                        paragraphs.append({
                            "speaker": f"Speaker {speaker}",
                            "text": paragraph_text
                        })
                else:
                    # Simple text fallback if paragraphs/diarization is not returned
                    transcript_text = alternatives[0].get("transcript", "")
                    paragraphs.append({
                        "speaker": "Speaker 0",
                        "text": transcript_text
                    })

        # Save usage to Supabase
        if supabase:
            try:
                supabase.table("usage_logs").insert({
                    "user_hash": user_hash,
                    "duration_seconds": duration_seconds
                }).execute()
            except Exception as db_err:
                print(f"Error saving usage log to Supabase: {db_err}")

        # Return results
        return {
            "duration_seconds": duration_seconds,
            "paragraphs": paragraphs if paragraphs else [{"speaker": "Speaker 0", "text": transcript_text}],
            "raw_transcript": transcript_text or " ".join(p["text"] for p in paragraphs)
        }
        
    except HTTPException as http_err:
        raise http_err
    except Exception as e:
        print(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

@app.post("/api/ai-features")
async def generate_ai_features(payload: AIFeaturesRequest):
    if not payload.transcript.strip():
        raise HTTPException(status_code=400, detail="Transcript is empty.")

    system_prompt = ""
    user_prompt = f"Transcript:\n{payload.transcript}\n\n"

    # Set up Prompts based on features requested
    if payload.feature_type == "summary":
        system_prompt = (
            "You are an expert AI note-taking and note-synthesizing assistant. "
            "Generate a highly structured summary of the provided transcript. "
            "Include a concise executive summary, followed by formal meeting minutes with timestamp references (if applicable), "
            "and list the main topics discussed. Use bullet points and clean markdown formatting."
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
        raise HTTPException(status_code=400, detail="Invalid feature_type. Must be 'summary', 'insights', or 'translation'.")

    try:
        # Query Hugging Face client
        model_name = "meta-llama/Meta-Llama-3-8B-Instruct"
        response = hf_client.chat_completion(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=2048,
            temperature=0.3
        )
        
        result_text = response.choices[0].message.content
        return {"result": result_text}
        
    except Exception as e:
        print(f"HF inference error: {e}")
        # Try a fallback model if llama fails
        try:
            fallback_model = "HuggingFaceH4/zephyr-7b-beta"
            response = hf_client.chat_completion(
                model=fallback_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=2048,
                temperature=0.3
            )
            result_text = response.choices[0].message.content
            return {"result": result_text}
        except Exception as fallback_err:
            print(f"Fallback model failed: {fallback_err}")
            raise HTTPException(status_code=500, detail=f"AI features generation failed: {str(e)}")

@app.post("/api/tts")
async def text_to_speech(payload: TTSRequest):
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Text payload is empty.")

    try:
        # Create unique temp file path
        fd, temp_file_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd) # Close file descriptor as edge-tts will open it

        communicate = edge_tts.Communicate(payload.text, payload.voice)
        await communicate.save(temp_file_path)
        
        # Return the generated audio file
        # We specify the media type and filename
        return FileResponse(
            path=temp_file_path,
            media_type="audio/mpeg",
            filename="voice_notes.mp3"
        )
        
    except Exception as e:
        print(f"TTS generation error: {e}")
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {str(e)}")

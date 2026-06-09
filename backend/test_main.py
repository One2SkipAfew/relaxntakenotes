import os
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient

# Mock environment variables before importing main to avoid missing config blocks
with patch.dict(os.environ, {
    "DEEPGRAM_API_KEY": "fake_deepgram_key",
    "SUPABASE_URL": "https://fake-supabase.supabase.co",
    "SUPABASE_KEY": "fake_supabase_key",
    "HF_TOKEN": "fake_hf_token",
    "MONTHLY_LIMIT_MINUTES": "100",
    "USER_MONTHLY_LIMIT_MINUTES": "10",
    "MAX_RECORDING_DURATION_MINUTES": "5"
}):
    # Initialize clients with patch to prevent exceptions during import
    with patch("supabase.create_client") as mock_create_client:
        mock_supabase = MagicMock()
        mock_create_client.return_value = mock_supabase
        from main import app, supabase

client = TestClient(app)

@pytest.fixture
def mock_supabase_queries():
    with patch("main.supabase") as mock_db:
        yield mock_db

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "online"
    assert "service" in response.json()

def test_api_status_under_limit(mock_supabase_queries):
    # Mock database to return under-limit usage
    # 2 mins user usage, 10 mins global usage
    mock_user_select = MagicMock()
    mock_user_select.execute.return_value = MagicMock(data=[{"duration_seconds": 120}])
    
    mock_global_select = MagicMock()
    mock_global_select.execute.return_value = MagicMock(data=[{"duration_seconds": 600}])
    
    # Setup chain of methods
    mock_supabase_queries.table.return_value.select.return_value.eq.return_value.gte.return_value = mock_user_select
    
    # We must patch get_usage_stats directly to bypass multiple table call chains or mock them carefully
    with patch("main.get_usage_stats") as mock_stats:
        mock_stats.return_value = (120, 600) # (user_seconds, global_seconds)
        
        response = client.get("/api/status", headers={"X-User-UUID": "test-user-uuid"})
        assert response.status_code == 200
        data = response.json()
        assert data["global_usage_minutes"] == 10.0
        assert data["user_usage_minutes"] == 2.0
        assert data["is_over_budget"] is False
        assert data["user_is_over_limit"] is False

def test_api_status_over_limit():
    with patch("main.get_usage_stats") as mock_stats:
        # User has consumed 12 hours (720 mins), global has consumed 110 hours (6600 mins)
        mock_stats.return_value = (720 * 60, 6600 * 60)
        
        response = client.get("/api/status", headers={"X-User-UUID": "test-user-uuid"})
        assert response.status_code == 200
        data = response.json()
        assert data["is_over_budget"] is True
        assert data["user_is_over_limit"] is True

def test_transcribe_audio_over_user_limit():
    with patch("main.get_usage_stats") as mock_stats:
        # User is over limit
        mock_stats.return_value = (20 * 60, 50 * 60) # limits are 10 mins user, 100 mins global
        
        # Create a mock file
        files = {"file": ("test.wav", b"fake audio content", "audio/wav")}
        response = client.post("/api/transcribe", files=files, headers={"X-User-UUID": "test-user-uuid"})
        
        assert response.status_code == 403
        assert "personal monthly transcription limit" in response.json()["detail"]

def test_transcribe_audio_successful():
    with patch("main.get_usage_stats") as mock_stats, \
         patch("main.deepgram_client") as mock_dg:
        
        # Under limit
        mock_stats.return_value = (0, 0)
        
        # Mock Deepgram transcription response
        mock_response = {
            "metadata": {"duration": 150.5},
            "results": {
                "channels": [
                    {
                        "alternatives": [
                            {
                                "transcript": "Hello world this is a test",
                                "words": [],
                                "paragraphs": {
                                    "paragraphs": [
                                        {
                                            "speaker": 0,
                                            "sentences": [{"text": "Hello world"}]
                                        },
                                        {
                                            "speaker": 1,
                                            "sentences": [{"text": "this is a test"}]
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            }
        }
        mock_dg.listen.prerecorded.v.return_value.transcribe_file.return_value = mock_response
        
        files = {"file": ("test.wav", b"fake audio content", "audio/wav")}
        response = client.post("/api/transcribe", files=files, headers={"X-User-UUID": "test-user-uuid"})
        
        assert response.status_code == 200
        data = response.json()
        assert data["duration_seconds"] == 150
        assert len(data["paragraphs"]) == 2
        assert data["paragraphs"][0]["speaker"] == "Speaker 0"
        assert data["paragraphs"][0]["text"] == "Hello world"

def test_ai_features_summary():
    with patch("main.hf_client") as mock_hf:
        # Mock HF completion response
        mock_choice = MagicMock()
        mock_choice.message.content = "This is a mock AI summary."
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_hf.chat_completion.return_value = mock_response
        
        payload = {
            "transcript": "Speaker 0: Hello and welcome.",
            "feature_type": "summary",
            "metadata": {"title": "Test Meeting"}
        }
        response = client.post("/api/ai-features", json=payload)
        
        assert response.status_code == 200
        assert response.json()["result"] == "This is a mock AI summary."
        mock_hf.chat_completion.assert_called_once()

@pytest.mark.asyncio
async def test_text_to_speech():
    with patch("edge_tts.Communicate") as mock_communicate:
        # Mock the async save method on Communicate
        mock_instance = MagicMock()
        mock_instance.save = AsyncMock()
        mock_communicate.return_value = mock_instance
        
        payload = {
            "text": "Hello world",
            "voice": "en-US-JennyNeural"
        }
        
        response = client.post("/api/tts", json=payload)
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"

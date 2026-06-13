import os
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient

# Mock environment variables before importing main
with patch.dict(os.environ, {
    "DEEPGRAM_API_KEY": "temp_deepgram_key",
    "SUPABASE_URL": "https://fake-supabase.supabase.co",
    "SUPABASE_KEY": "fake_supabase_key",
    "HF_TOKEN": "fake_hf_token",
    "AI_PROVIDER": "hf-inference",
    "AI_MODEL": "meta-llama/Llama-3.3-70B-Instruct",
    "HF_ENDPOINT_URL": "",
    "MONTHLY_LIMIT_MINUTES": "100",
    "USER_MONTHLY_LIMIT_MINUTES": "10",
    "MAX_RECORDING_DURATION_MINUTES": "5",
    "ALLOWED_ORIGINS": "http://localhost:5173,https://relaxntakenotes.africa",
}):
    with patch("supabase.create_client") as mock_create_client:
        mock_supabase = MagicMock()
        mock_create_client.return_value = mock_supabase
        from main import app, supabase

client = TestClient(app)


def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert data["version"] == "1.1.0"
    assert "service" in data


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_api_status_under_limit():
    with patch("main.get_usage_stats") as mock_stats:
        mock_stats.return_value = (120, 600)  # 2 min user, 10 min global
        response = client.get("/api/status", headers={"X-User-UUID": "test-user-uuid"})
        assert response.status_code == 200
        data = response.json()
        assert data["global_usage_minutes"] == 10.0
        assert data["user_usage_minutes"] == 2.0
        assert data["is_over_budget"] is False
        assert data["user_is_over_limit"] is False


def test_api_status_over_limit():
    with patch("main.get_usage_stats") as mock_stats:
        mock_stats.return_value = (720 * 60, 6600 * 60)
        response = client.get("/api/status", headers={"X-User-UUID": "test-user-uuid"})
        assert response.status_code == 200
        data = response.json()
        assert data["is_over_budget"] is True
        assert data["user_is_over_limit"] is True


def test_transcribe_audio_over_user_limit():
    with patch("main.get_usage_stats") as mock_stats:
        mock_stats.return_value = (20 * 60, 50 * 60)
        files = {"file": ("test.wav", b"fake audio content", "audio/wav")}
        response = client.post("/api/transcribe", files=files, headers={"X-User-UUID": "test-user-uuid"})
        assert response.status_code == 403
        assert "monthly transcription limit" in response.json()["detail"]


def test_transcribe_audio_successful():
    with patch("main.get_usage_stats") as mock_stats, \
         patch("main.deepgram_client") as mock_dg:
        mock_stats.return_value = (0, 0)
        mock_response = {
            "metadata": {"duration": 150.5},
            "results": {
                "channels": [{
                    "alternatives": [{
                        "transcript": "Hello world this is a test",
                        "words": [],
                        "paragraphs": {
                            "paragraphs": [
                                {"speaker": 0, "sentences": [{"text": "Hello world"}]},
                                {"speaker": 1, "sentences": [{"text": "this is a test"}]},
                            ]
                        },
                    }]
                }]
            },
        }
        mock_dg.listen.prerecorded.v.return_value.transcribe_file.return_value = mock_response
        files = {"file": ("test.wav", b"fake audio content", "audio/wav")}
        response = client.post("/api/transcribe", files=files, headers={"X-User-UUID": "test-user-uuid"})
        assert response.status_code == 200
        data = response.json()
        assert data["duration_seconds"] == 150
        assert len(data["paragraphs"]) == 2
        assert data["paragraphs"][0]["speaker"] == "Speaker 0"


def test_ai_features_summary():
    with patch("main.hf_client") as mock_hf:
        mock_choice = MagicMock()
        mock_choice.message.content = "This is a mock AI summary."
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_hf.chat_completion.return_value = mock_response

        payload = {
            "transcript": "Speaker 0: Hello and welcome.",
            "feature_type": "summary",
            "metadata": {"title": "Test Meeting"},
        }
        response = client.post("/api/ai-features", json=payload)
        assert response.status_code == 200
        assert response.json()["result"] == "This is a mock AI summary."


def test_ai_features_invalid_type():
    payload = {
        "transcript": "Speaker 0: Hello.",
        "feature_type": "invalid_type",
    }
    response = client.post("/api/ai-features", json=payload)
    assert response.status_code == 422  # Pydantic validation error


@pytest.mark.asyncio
async def test_text_to_speech():
    with patch("edge_tts.Communicate") as mock_communicate:
        mock_instance = MagicMock()
        mock_instance.save = AsyncMock()
        mock_communicate.return_value = mock_instance
        payload = {"text": "Hello world", "voice": "en-US-JennyNeural"}
        response = client.post("/api/tts", json=payload)
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"


def test_ai_features_service_unavailable():
    with patch("main.hf_client.chat_completion", side_effect=Exception("Connection refused")):
        payload = {
            "transcript": "Speaker 0: Good morning.",
            "feature_type": "summary",
        }
        response = client.post("/api/ai-features", json=payload)
        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"].lower()


def test_text_to_speech_fallback():
    with patch("edge_tts.Communicate", side_effect=Exception("Service Unavailable")):
        payload = {"text": "Hello fallback", "voice": "en-US-JennyNeural"}
        response = client.post("/api/tts", json=payload)
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"
        assert len(response.content) > 0

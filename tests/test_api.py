"""Tests for API endpoints."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from claude_code_tts_server.api.routes import router
from claude_code_tts_server.core.audio_manager import QueueStatus
from claude_code_tts_server.summarizers.base import SummaryResult


@pytest.fixture
def mock_audio_manager():
    """Create a mock audio manager (pipeline)."""
    manager = AsyncMock()
    manager.add_message = AsyncMock(return_value="test-message-id")
    manager.add_request = AsyncMock(return_value="test-request-id")
    manager.get_status = MagicMock(return_value=QueueStatus(
        pending_requests=0,
        pending_messages=0,
        ready_audio=0,
        is_playing=False,
        current_text=None,
    ))
    manager.clear_queue = AsyncMock(return_value=5)
    manager.skip_current = AsyncMock(return_value=True)
    return manager


@pytest.fixture
def mock_summarizer():
    """Create a mock summarizer."""
    summarizer = AsyncMock()
    summarizer.summarize = AsyncMock(return_value=SummaryResult(
        text="Test summary",
        model_used="test-model",
        tokens_used=100,
    ))
    summarizer.health_check = AsyncMock(return_value=True)
    return summarizer


@pytest.fixture
def app(mock_audio_manager, mock_summarizer):
    """Create test FastAPI app."""
    app = FastAPI()
    app.include_router(router)
    app.state.audio_manager = mock_audio_manager
    app.state.summarizer = mock_summarizer
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    def test_health_check(self, client, mock_audio_manager, mock_summarizer):
        """Test health check returns OK."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["tts_ready"] is True
        assert data["summarizer_ready"] is True
        assert data["queue_depth"] == 0


class TestSpeakEndpoint:
    """Tests for /speak endpoint."""

    def test_speak_success(self, client, mock_audio_manager):
        """Test successful speak request."""
        response = client.post("/speak", json={"text": "Hello world"})

        assert response.status_code == 200
        data = response.json()
        assert data["message_id"] == "test-message-id"
        assert data["status"] == "queued"

        mock_audio_manager.add_message.assert_called_once_with("Hello world")

    def test_speak_empty_text(self, client):
        """Test speak with empty text."""
        response = client.post("/speak", json={"text": "   "})

        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()


class TestSummarizeEndpoint:
    """Tests for /summarize endpoint."""

    def test_summarize_with_content(self, client, mock_audio_manager, sample_transcript_jsonl):
        """Test summarize with transcript content queues immediately."""
        response = client.post(
            "/summarize",
            json={"transcript_content": sample_transcript_jsonl},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["message_id"] == "test-request-id"
        assert data["status"] == "queued"

        # Should queue request, not call summarizer directly
        mock_audio_manager.add_request.assert_called_once()

    def test_summarize_empty_content(self, client):
        """Test summarize with empty content."""
        response = client.post(
            "/summarize",
            json={"transcript_content": ""},
        )

        assert response.status_code == 400

    def test_summarize_no_input(self, client):
        """Test summarize with no input."""
        response = client.post("/summarize", json={})

        assert response.status_code == 422  # Pydantic validation error


class TestPermissionEndpoint:
    """Tests for /permission endpoint."""

    def test_permission_success(self, client, mock_audio_manager):
        """Test successful permission request queues immediately."""
        response = client.post(
            "/permission",
            json={
                "tool_name": "Bash",
                "tool_input": {"command": "npm install"},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["message_id"] == "test-request-id"
        assert data["status"] == "queued"

        # Should queue request, not call summarizer directly
        mock_audio_manager.add_request.assert_called_once()

    def test_permission_with_description(self, client, mock_audio_manager):
        """Test permission with description in tool input."""
        response = client.post(
            "/permission",
            json={
                "tool_name": "Bash",
                "tool_input": {
                    "command": "npm install",
                    "description": "Install dependencies",
                },
            },
        )

        assert response.status_code == 200


class TestQueueEndpoints:
    """Tests for queue management endpoints."""

    def test_get_queue_status(self, client, mock_audio_manager):
        """Test getting queue status."""
        mock_audio_manager.get_status.return_value = QueueStatus(
            pending_requests=2,
            pending_messages=1,
            ready_audio=3,
            is_playing=True,
            current_text="Playing this",
        )

        response = client.get("/queue")

        assert response.status_code == 200
        data = response.json()
        assert data["pending_requests"] == 2
        assert data["pending_messages"] == 1
        assert data["ready_audio"] == 3
        assert data["is_playing"] is True
        assert data["current_text"] == "Playing this"

    def test_clear_queue(self, client, mock_audio_manager):
        """Test clearing the queue."""
        response = client.post("/queue/clear")

        assert response.status_code == 200
        data = response.json()
        assert data["cleared"] == 5
        assert data["status"] == "ok"

        mock_audio_manager.clear_queue.assert_called_once()

    def test_skip_current(self, client, mock_audio_manager):
        """Test skipping current audio."""
        response = client.post("/queue/skip")

        assert response.status_code == 200
        data = response.json()
        assert data["skipped"] is True
        assert data["status"] == "ok"

        mock_audio_manager.skip_current.assert_called_once()

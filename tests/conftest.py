"""Pytest fixtures for Claude Code TTS Server tests."""

import json
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from claude_code_tts_server.config import AudioConfig, ServerConfig, SummarizerConfig, TTSConfig
from claude_code_tts_server.core.audio_manager import AudioManager
from claude_code_tts_server.summarizers.base import SummaryResult
from claude_code_tts_server.tts.base import TTSInterface


@pytest.fixture
def sample_audio():
    """Generate sample audio data."""
    return np.zeros(24000, dtype=np.float32)  # 1 second of silence


@pytest.fixture
def mock_tts(sample_audio):
    """Create a mock TTS backend."""
    tts = AsyncMock(spec=TTSInterface)
    tts.synthesize.return_value = sample_audio
    tts.get_sample_rate.return_value = 24000
    return tts


@pytest.fixture
def mock_summarizer():
    """Create a mock summarizer."""
    summarizer = AsyncMock()
    summarizer.summarize.return_value = SummaryResult(
        text="Test summary",
        model_used="test-model",
        tokens_used=100,
    )
    summarizer.health_check.return_value = True
    return summarizer


@pytest.fixture
def audio_config():
    """Create default audio config."""
    return AudioConfig(
        interrupt=True,
        min_duration=0.1,  # Short for testing
        queue=True,
        max_queue=10,
        interrupt_chime=False,  # Disable for testing
        drop_sound=False,  # Disable for testing
    )


@pytest.fixture
def tts_config():
    """Create default TTS config."""
    return TTSConfig(
        backend="kokoro",
        kokoro_voice="af_heart",
        kokoro_lang="a",
    )


@pytest.fixture
def summarizer_config():
    """Create default summarizer config."""
    return SummarizerConfig(
        backend="groq",
        groq_api_key="test-key",
        groq_model_large="test-model-large",
        groq_model_small="test-model-small",
    )


@pytest.fixture
def server_config(tts_config, summarizer_config, audio_config):
    """Create default server config."""
    return ServerConfig(
        host="127.0.0.1",
        port=20202,
        log_level="WARNING",
        tts=tts_config,
        summarizer=summarizer_config,
        audio=audio_config,
    )


def _entries_to_jsonl(entries: list[dict]) -> str:
    """Convert list of entries to JSONL string."""
    return "\n".join(json.dumps(entry) for entry in entries)


@pytest.fixture
def sample_transcript_jsonl():
    """Sample transcript JSONL content."""
    entries = [
        {
            "type": "user",
            "message": {
                "content": [{"type": "text", "text": "Hello, can you help me?"}]
            }
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Of course! I'd be happy to help you."}
                ]
            }
        },
    ]
    return _entries_to_jsonl(entries)


@pytest.fixture
def sample_transcript_with_tools():
    """Sample transcript with tool calls."""
    entries = [
        {
            "type": "user",
            "message": {
                "content": [{"type": "text", "text": "Run the tests"}]
            }
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "I'll run the tests for you."},
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "pytest", "description": "Run tests"}
                    }
                ]
            }
        },
    ]
    return _entries_to_jsonl(entries)


@pytest.fixture
def sample_transcript_with_interrupt():
    """Sample transcript with an interrupt."""
    entries = [
        {
            "type": "user",
            "message": {
                "content": [{"type": "text", "text": "Delete all files"}]
            }
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "rm -rf /"}
                    }
                ]
            }
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": "The user doesn't want to proceed with this action."
                    }
                ]
            }
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Understood, I won't proceed with that command."}
                ]
            }
        },
    ]
    return _entries_to_jsonl(entries)

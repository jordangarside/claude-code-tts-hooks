"""Configuration for Claude Code TTS Server."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TTSConfig(BaseSettings):
    """TTS backend configuration."""

    model_config = SettingsConfigDict(
        env_prefix="TTS_",
        env_file=".env",
        populate_by_name=True,
        extra="ignore",
    )

    backend: Literal["kokoro", "groq", "elevenlabs"] = Field(
        default="kokoro", alias="TTS_BACKEND"
    )

    # Kokoro settings (local, free)
    kokoro_voice: str = Field(default="af_heart", alias="TTS_KOKORO_VOICE")
    kokoro_lang: str = Field(default="a", alias="TTS_KOKORO_LANG")

    # Groq PlayAI settings (API, fast)
    groq_api_key: str | None = Field(default=None, alias="TTS_GROQ_API_KEY")
    groq_voice: str = Field(default="Arista-PlayAI", alias="TTS_GROQ_VOICE")
    groq_model: str = Field(default="playai-tts", alias="TTS_GROQ_MODEL")

    # ElevenLabs settings (API, high quality)
    elevenlabs_api_key: str | None = Field(default=None, alias="TTS_ELEVENLABS_API_KEY")
    elevenlabs_voice: str = Field(default="Daniel", alias="TTS_ELEVENLABS_VOICE")
    elevenlabs_model: str = Field(default="eleven_multilingual_v2", alias="TTS_ELEVENLABS_MODEL")


class SummarizerConfig(BaseSettings):
    """Summarizer configuration."""

    model_config = SettingsConfigDict(
        env_prefix="SUMMARY_",
        env_file=".env",
        populate_by_name=True,
        extra="ignore",
    )

    backend: Literal["groq", "ollama"] = Field(default="groq", alias="SUMMARY_BACKEND")
    groq_api_key: str | None = Field(default=None, alias="SUMMARY_GROQ_API_KEY")
    groq_model_large: str = Field(
        default="openai/gpt-oss-120b", alias="SUMMARY_GROQ_MODEL_LARGE"
    )
    groq_model_small: str = Field(
        default="llama-3.1-8b-instant", alias="SUMMARY_GROQ_MODEL_SMALL"
    )
    ollama_url: str = Field(
        default="http://localhost:11434", alias="SUMMARY_OLLAMA_URL"
    )
    ollama_model_large: str = Field(
        default="qwen3:4b-instruct-2507-q4_K_M", alias="SUMMARY_OLLAMA_MODEL_LARGE"
    )
    ollama_model_small: str = Field(
        default="qwen3:4b-instruct-2507-q4_K_M", alias="SUMMARY_OLLAMA_MODEL_SMALL"
    )


class AudioConfig(BaseSettings):
    """Audio playback configuration."""

    model_config = SettingsConfigDict(env_prefix="AUDIO_", env_file=".env", extra="ignore")

    interrupt: bool = True
    min_duration: float = 1.5
    queue: bool = True
    max_queue: int = 10
    interrupt_chime: bool = True
    drop_sound: bool = True
    speed: float = 1.0  # Playback speed multiplier (1.0 = normal, 1.3 = 30% faster)


class ServerConfig(BaseSettings):
    """Server configuration."""

    model_config = SettingsConfigDict(
        env_prefix="TTS_SERVER_",
        env_nested_delimiter="__",
        env_file=".env",
        populate_by_name=True,
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=20202, alias="SUMMARY_AUDIO_PORT")
    log_level: Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    tts: TTSConfig = Field(default_factory=TTSConfig)
    summarizer: SummarizerConfig = Field(default_factory=SummarizerConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)

    @classmethod
    def from_cli_args(cls, **kwargs) -> "ServerConfig":
        """Create config from CLI arguments, merged with env vars."""
        # Filter out None values (unset CLI args)
        cli_args = {k: v for k, v in kwargs.items() if v is not None}

        # Map CLI args to nested config
        tts_args = {}
        summarizer_args = {}
        audio_args = {}
        server_args = {}

        for key, value in cli_args.items():
            if key in ("voice", "lang"):
                tts_args[key] = value
            elif key in ("summarizer_backend",):
                summarizer_args["backend"] = value
            elif key in ("interrupt", "min_duration", "queue", "max_queue",
                        "interrupt_chime", "drop_sound"):
                # Convert CLI naming to config naming
                audio_args[key.replace("-", "_")] = value
            elif key in ("host", "port", "log_level"):
                server_args[key] = value

        # Create nested configs with CLI overrides
        tts = TTSConfig(**tts_args) if tts_args else TTSConfig()
        summarizer = SummarizerConfig(**summarizer_args) if summarizer_args else SummarizerConfig()
        audio = AudioConfig(**audio_args) if audio_args else AudioConfig()

        return cls(tts=tts, summarizer=summarizer, audio=audio, **server_args)

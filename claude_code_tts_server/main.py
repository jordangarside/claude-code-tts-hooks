"""Main entry point for Claude Code TTS Server."""

import logging
import signal
import sys
import warnings
from contextlib import asynccontextmanager

import click
import uvicorn
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from .api.routes import router
from .core.context import clear_request_id, set_request_id
from .config import AudioConfig, ServerConfig, SummarizerConfig, TTSConfig
from .core.audio_manager import AudioManager
from .summarizers.base import SummarizerInterface
from .summarizers.groq import GroqSummarizer
from .summarizers.ollama import OllamaSummarizer
from .tts.base import TTSInterface
from .tts.kokoro import KokoroTTS


def create_summarizer(config: SummarizerConfig) -> SummarizerInterface:
    """Create the appropriate summarizer based on config."""
    if config.backend == "ollama":
        return OllamaSummarizer(config)
    else:
        return GroqSummarizer(config)


def create_tts(config: TTSConfig) -> TTSInterface:
    """Create the appropriate TTS backend based on config."""
    if config.backend == "kokoro":
        return KokoroTTS(config)
    elif config.backend == "groq":
        raise NotImplementedError("Groq TTS backend not yet implemented")
    elif config.backend == "elevenlabs":
        raise NotImplementedError("ElevenLabs TTS backend not yet implemented")
    else:
        raise ValueError(f"Unknown TTS backend: {config.backend}")

# Suppress warnings from dependencies
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to set request ID for each request."""

    async def dispatch(self, request: Request, call_next):
        request_id = set_request_id()
        try:
            response = await call_next(request)
            return response
        finally:
            clear_request_id()


class ColorFormatter(logging.Formatter):
    """Custom formatter with colors, timestamps, and request ID support."""

    COLORS = {
        logging.DEBUG: "\033[36m",  # Cyan
        logging.INFO: "\033[32m",  # Green
        logging.WARNING: "\033[33m",  # Yellow
        logging.ERROR: "\033[31m",  # Red
    }
    RESET = "\033[0m"
    DIM = "\033[2m"

    def format(self, record):
        from .core.context import get_request_id

        color = self.COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname:<5}{self.RESET}"

        # Add request ID if available
        request_id = get_request_id()
        if request_id:
            record.request_id = f" {self.DIM}[{request_id}]{self.RESET}"
        else:
            record.request_id = ""

        return super().format(record)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure logging with colored output."""
    logger = logging.getLogger("tts-server")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers
    logger.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(
        ColorFormatter(
            fmt="[%(asctime)s.%(msecs)03d][%(levelname)s]%(request_id)s %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(handler)

    # Also configure uvicorn's logger to be less verbose
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return logger


def create_app(config: ServerConfig) -> FastAPI:
    """Create and configure the FastAPI application."""
    log = logging.getLogger("tts-server")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage application lifecycle."""
        # Startup
        log.info("Starting TTS server...")

        # Check rubberband availability if speed is configured
        if config.audio.speed != 1.0:
            from .core.sounds import _check_rubberband_available
            _check_rubberband_available()

        # Initialize TTS backend
        tts = create_tts(config.tts)
        await tts.initialize()

        # Initialize summarizer
        summarizer = create_summarizer(config.summarizer)

        # Initialize audio pipeline (manages summarization, TTS, and playback)
        audio_manager = AudioManager(config.audio, tts, summarizer)
        await audio_manager.start()

        # Store in app state
        app.state.tts = tts
        app.state.summarizer = summarizer
        app.state.audio_manager = audio_manager
        app.state.config = config

        # Log full config at startup
        _log_startup_config(config)

        yield

        # Shutdown
        log.info("Shutting down...")
        await audio_manager.stop()
        await tts.cleanup()
        await summarizer.close()

    app = FastAPI(
        title="Claude Code TTS Server",
        description="Audio feedback for Claude Code via text-to-speech",
        version="0.2.0",
        lifespan=lifespan,
    )

    app.add_middleware(RequestIDMiddleware)
    app.include_router(router)

    return app


def _log_startup_config(config: ServerConfig) -> None:
    """Log full configuration at startup."""
    log = logging.getLogger("tts-server")

    log.info(f"Server ready on http://{config.host}:{config.port}")

    # TTS config
    tts = config.tts
    if tts.backend == "kokoro":
        log.info(f"TTS: {tts.backend} (voice={tts.kokoro_voice}, lang={tts.kokoro_lang})")
    elif tts.backend == "groq":
        log.info(f"TTS: {tts.backend} (voice={tts.groq_voice}, model={tts.groq_model})")
    elif tts.backend == "elevenlabs":
        log.info(f"TTS: {tts.backend} (voice={tts.elevenlabs_voice}, model={tts.elevenlabs_model})")

    # Summarizer config
    summ = config.summarizer
    if summ.backend == "groq":
        log.info(f"Summarizer: {summ.backend} (large={summ.groq_model_large}, small={summ.groq_model_small})")
    elif summ.backend == "ollama":
        log.info(f"Summarizer: {summ.backend} @ {summ.ollama_url} (large={summ.ollama_model_large}, small={summ.ollama_model_small})")

    # Audio config
    audio = config.audio
    speed_str = f", speed={audio.speed}x" if audio.speed != 1.0 else ""
    log.info(
        f"Audio: interrupt={audio.interrupt}, queue={audio.queue}, "
        f"min_duration={audio.min_duration}s, max_queue={audio.max_queue}{speed_str}"
    )


@click.command()
@click.option("--host", default=None, help="Host to bind to (env: TTS_SERVER_HOST)")
@click.option("--port", default=None, type=int, help="Port to listen on (env: SUMMARY_AUDIO_PORT)")
# TTS backend options
@click.option(
    "--tts",
    default=None,
    type=click.Choice(["kokoro", "groq", "elevenlabs"]),
    help="TTS backend (env: TTS_BACKEND)",
)
@click.option("--kokoro-voice", default=None, help="Kokoro voice (env: TTS_KOKORO_VOICE)")
@click.option("--kokoro-lang", default=None, help="Kokoro language code (env: TTS_KOKORO_LANG)")
@click.option("--tts-groq-voice", default=None, help="Groq PlayAI voice (env: TTS_GROQ_VOICE)")
@click.option("--tts-groq-model", default=None, help="Groq TTS model (env: TTS_GROQ_MODEL)")
@click.option("--elevenlabs-voice", default=None, help="ElevenLabs voice (env: TTS_ELEVENLABS_VOICE)")
@click.option("--elevenlabs-model", default=None, help="ElevenLabs model (env: TTS_ELEVENLABS_MODEL)")
# Audio options
@click.option("--interrupt/--no-interrupt", default=None, help="Allow interrupts (env: AUDIO_INTERRUPT)")
@click.option(
    "--min-duration",
    default=None,
    type=float,
    help="Seconds before interrupt allowed (env: AUDIO_MIN_DURATION)",
)
@click.option("--queue/--no-queue", default=None, help="Queue all messages (env: AUDIO_QUEUE)")
@click.option("--max-queue", default=None, type=int, help="Max queue depth (env: AUDIO_MAX_QUEUE)")
@click.option(
    "--interrupt-chime/--no-interrupt-chime",
    default=None,
    help="Play chime on interrupt (env: AUDIO_INTERRUPT_CHIME)",
)
@click.option(
    "--drop-sound/--no-drop-sound",
    default=None,
    help="Play sound when messages dropped (env: AUDIO_DROP_SOUND)",
)
@click.option(
    "--speed",
    default=None,
    type=float,
    help="Playback speed multiplier, e.g. 1.5 for 50%% faster (env: AUDIO_SPEED)",
)
@click.option(
    "--log-level",
    default=None,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
    help="Log level (env: TTS_SERVER_LOG_LEVEL)",
)
# Summarizer options
@click.option(
    "--summarizer",
    default=None,
    type=click.Choice(["groq", "ollama"]),
    help="Summarizer backend (env: SUMMARY_BACKEND)",
)
@click.option(
    "--ollama-model-large",
    default=None,
    help="Ollama model for long responses (env: SUMMARY_OLLAMA_MODEL_LARGE)",
)
@click.option(
    "--ollama-model-small",
    default=None,
    help="Ollama model for short responses (env: SUMMARY_OLLAMA_MODEL_SMALL)",
)
@click.option(
    "--ollama-url",
    default=None,
    help="Ollama server URL (env: SUMMARY_OLLAMA_URL)",
)
def main(
    host: str | None,
    port: int | None,
    tts: str | None,
    kokoro_voice: str | None,
    kokoro_lang: str | None,
    tts_groq_voice: str | None,
    tts_groq_model: str | None,
    elevenlabs_voice: str | None,
    elevenlabs_model: str | None,
    interrupt: bool | None,
    min_duration: float | None,
    queue: bool | None,
    max_queue: int | None,
    interrupt_chime: bool | None,
    drop_sound: bool | None,
    speed: float | None,
    log_level: str | None,
    summarizer: str | None,
    ollama_model_large: str | None,
    ollama_model_small: str | None,
    ollama_url: str | None,
) -> None:
    """Claude Code TTS Server - Audio feedback via text-to-speech."""
    # Load base config from env vars first
    tts_config = TTSConfig()
    summarizer_config = SummarizerConfig()
    audio_config = AudioConfig()
    server_config = ServerConfig()

    # Override with CLI args if provided
    tts_overrides = {
        k: v for k, v in {
            "backend": tts,
            "kokoro_voice": kokoro_voice,
            "kokoro_lang": kokoro_lang,
            "groq_voice": tts_groq_voice,
            "groq_model": tts_groq_model,
            "elevenlabs_voice": elevenlabs_voice,
            "elevenlabs_model": elevenlabs_model,
        }.items() if v is not None
    }
    if tts_overrides:
        tts_config = TTSConfig(**{**tts_config.model_dump(), **tts_overrides})

    summarizer_overrides = {
        k: v for k, v in {
            "backend": summarizer,
            "ollama_model_large": ollama_model_large,
            "ollama_model_small": ollama_model_small,
            "ollama_url": ollama_url,
        }.items() if v is not None
    }
    if summarizer_overrides:
        summarizer_config = SummarizerConfig(**{**summarizer_config.model_dump(), **summarizer_overrides})

    audio_overrides = {
        k: v for k, v in {
            "interrupt": interrupt,
            "min_duration": min_duration,
            "queue": queue,
            "max_queue": max_queue,
            "interrupt_chime": interrupt_chime,
            "drop_sound": drop_sound,
            "speed": speed,
        }.items() if v is not None
    }
    if audio_overrides:
        audio_config = AudioConfig(**{**audio_config.model_dump(), **audio_overrides})

    server_overrides = {k: v for k, v in {"host": host, "port": port, "log_level": log_level}.items() if v is not None}
    config = ServerConfig(
        **{**{"host": server_config.host, "port": server_config.port, "log_level": server_config.log_level}, **server_overrides},
        tts=tts_config,
        summarizer=summarizer_config,
        audio=audio_config,
    )

    # Setup logging with resolved log level
    setup_logging(config.log_level)
    log = logging.getLogger("tts-server")

    # Create app
    app = create_app(config)

    # Run server
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="warning",  # Suppress uvicorn logs, we have our own
    )


if __name__ == "__main__":
    main()

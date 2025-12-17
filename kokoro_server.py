#!/usr/bin/env python3
"""
Kokoro TTS Server for Claude Code audio hooks.

Usage:
    kokoro-server [options]

Options:
    --port                Port to listen on (default: 20202)
    --voice               Kokoro voice (default: af_heart)
    --lang                Language code (default: a)
    --interrupt           Allow interrupts (default: true)
    --no-interrupt        Disable interrupts
    --min-duration        Seconds before interrupt allowed (default: 1.5)
    --queue               Queue all messages (default: true)
    --no-queue            Only play latest message
    --max-queue           Max queue depth (default: 10)
    --interrupt-chime     Play chime on interrupt (default: true)
    --no-interrupt-chime  Disable interrupt chime
    --drop-sound          Play sound when messages dropped (default: true)
    --no-drop-sound       Disable drop sound
    --log-level           Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)
"""

import argparse
import asyncio
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import warnings
from collections import deque
from dataclasses import dataclass
from typing import Optional

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)


# =============================================================================
# Logging
# =============================================================================

class ColorFormatter(logging.Formatter):
    """Custom formatter with colors and millisecond timestamps."""

    COLORS = {
        logging.DEBUG: "\033[36m",     # Cyan
        logging.INFO: "\033[32m",      # Green
        logging.WARNING: "\033[33m",   # Yellow
        logging.ERROR: "\033[31m",     # Red
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname:<5}{self.RESET}"
        return super().format(record)


def setup_logging(level: str = "INFO"):
    """Configure logging with colored output."""
    logger = logging.getLogger("kokoro")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter(
        fmt="%(asctime)s.%(msecs)03d [kokoro] %(levelname)s %(message)s",
        datefmt="%H:%M:%S"
    ))
    logger.addHandler(handler)
    return logger


log = logging.getLogger("kokoro")


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class Config:
    port: int = 20202
    voice: str = "af_heart"
    lang: str = "a"
    interrupt: bool = True
    min_duration: float = 1.5
    queue: bool = True
    max_queue: int = 10
    interrupt_chime: bool = True
    drop_sound: bool = True
    log_level: str = "INFO"


config = Config()


# =============================================================================
# Data Types
# =============================================================================

@dataclass
class Message:
    id: str
    text: str
    timestamp: float


@dataclass
class ReadyAudio:
    message_id: str
    audio_file: str
    text: str


# =============================================================================
# Global State
# =============================================================================

# TTS pipeline (initialized in main)
pipeline = None

# Message management
pending_messages: deque[Message] = deque()  # Messages waiting to be generated
pending_lock = asyncio.Lock()

ready_audio: deque[ReadyAudio] = deque()  # Generated audio ready to play
ready_lock = asyncio.Lock()

# Playback state
current_process: Optional[subprocess.Popen] = None
play_start_time: Optional[float] = None

# Sound effect files
chime_file: Optional[str] = None
drop_file: Optional[str] = None

# Control
shutdown_event = asyncio.Event()
new_message_event = asyncio.Event()
audio_ready_event = asyncio.Event()


# =============================================================================
# Sound Generation
# =============================================================================

def generate_chime(sample_rate=24000):
    """Two-note chime (G5 → C6) for interrupts."""
    import numpy as np

    def make_note(freq, duration, amplitude=0.25):
        t = np.linspace(0, duration, int(sample_rate * duration), False)
        note = amplitude * np.sin(2 * np.pi * freq * t)
        note += amplitude * 0.3 * np.sin(2 * np.pi * freq * 2 * t)
        note += amplitude * 0.1 * np.sin(2 * np.pi * freq * 3 * t)
        envelope = np.exp(-t * 8)
        attack = int(len(t) * 0.05)
        envelope[:attack] *= np.linspace(0, 1, attack)
        return note * envelope

    note1 = make_note(784, 0.08)  # G5
    note2 = make_note(1047, 0.08)  # C6
    gap = np.zeros(int(sample_rate * 0.03))
    chime = np.concatenate([note1, gap, note2])

    fade = int(sample_rate * 0.02)
    if fade > 0:
        chime[-fade:] *= np.linspace(1, 0, fade)

    return chime.astype(np.float32)


def generate_drop_tone(sample_rate=24000):
    """Soft kalimba-like pluck for dropped messages."""
    import numpy as np

    duration = 0.15
    t = np.linspace(0, duration, int(sample_rate * duration), False)

    # Base frequency - E5, gentle and musical
    freq = 659

    # Fundamental with decaying harmonics (kalimba/music box character)
    tone = np.sin(2 * np.pi * freq * t)
    tone += 0.5 * np.sin(2 * np.pi * freq * 2 * t) * np.exp(-t * 20)  # 2nd harmonic, fast decay
    tone += 0.25 * np.sin(2 * np.pi * freq * 3 * t) * np.exp(-t * 30)  # 3rd harmonic, faster decay
    tone += 0.1 * np.sin(2 * np.pi * freq * 4 * t) * np.exp(-t * 40)  # 4th harmonic

    # Pluck envelope - quick attack, smooth decay
    attack_time = 0.005
    attack_samples = int(sample_rate * attack_time)
    envelope = np.exp(-t * 10)
    envelope[:attack_samples] = np.linspace(0, 1, attack_samples)

    pluck = tone * envelope * 0.18

    # Soft fade out
    fade = int(sample_rate * 0.03)
    pluck[-fade:] *= np.linspace(1, 0, fade)

    return pluck.astype(np.float32)


def save_audio(audio, sample_rate=24000) -> str:
    """Save audio to temp WAV file."""
    import soundfile as sf
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        path = f.name
    sf.write(path, audio, sample_rate)
    return path


def init_sounds():
    """Generate sound effect files."""
    global chime_file, drop_file
    chime_file = save_audio(generate_chime())
    drop_file = save_audio(generate_drop_tone())
    log.debug(f"Sounds ready: chime={chime_file}, drop={drop_file}")


def cleanup_sounds():
    """Delete sound effect files."""
    global chime_file, drop_file
    for f in [chime_file, drop_file]:
        if f:
            try:
                os.unlink(f)
            except:
                pass
    chime_file = drop_file = None


# =============================================================================
# Audio Playback
# =============================================================================

def get_player():
    """Get audio player command for this platform."""
    if sys.platform == 'darwin':
        return ['afplay']
    for player in [['mpv', '--no-terminal'], ['paplay'], ['aplay']]:
        try:
            if subprocess.run(['which', player[0]], capture_output=True).returncode == 0:
                return player
        except:
            pass
    return None


def play_sound_async(audio_file: str):
    """Play sound without blocking (fire-and-forget)."""
    player = get_player()
    if player and audio_file:
        subprocess.Popen(player + [audio_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def play_drop_sound():
    """Play drop tone if enabled."""
    if config.drop_sound and drop_file:
        log.debug("Drop tone")
        play_sound_async(drop_file)


async def play_chime():
    """Play interrupt chime if enabled."""
    if config.interrupt_chime and chime_file:
        log.debug("Chime")
        player = get_player()
        if player:
            proc = subprocess.Popen(player + [chime_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Wait briefly for chime (but not forever)
            for _ in range(10):  # Max 0.5s
                if proc.poll() is not None:
                    break
                await asyncio.sleep(0.05)
            if proc.poll() is None:
                proc.terminate()


def stop_current_audio():
    """Stop currently playing audio."""
    global current_process, play_start_time
    if current_process and current_process.poll() is None:
        current_process.terminate()
        try:
            current_process.wait(timeout=0.1)
        except:
            current_process.kill()
    current_process = None
    play_start_time = None


# =============================================================================
# TTS Generation
# =============================================================================

def generate_tts(text: str):
    """Generate TTS audio from text."""
    import numpy as np
    all_audio = []
    for _, _, audio in pipeline(text, voice=config.voice):
        all_audio.append(audio)
    if not all_audio:
        return None
    return np.concatenate(all_audio)


# =============================================================================
# Message Handling
# =============================================================================

async def add_message(text: str):
    """Add a new message, handling queue limits and drops."""
    msg = Message(id=str(uuid.uuid4()), text=text, timestamp=time.time())

    async with pending_lock:
        if config.queue:
            # Queue mode: add to queue, drop oldest if over limit
            while len(pending_messages) >= config.max_queue:
                dropped = pending_messages.popleft()
                log.warning(f"Queue full, dropped: {dropped.text[:50]}...")
                play_drop_sound()
            pending_messages.append(msg)
        else:
            # No-queue mode: replace all pending with latest
            while pending_messages:
                dropped = pending_messages.popleft()
                log.warning(f"Dropped (latest-only): {dropped.text[:50]}...")
                play_drop_sound()
            pending_messages.append(msg)

    log.info(f"Message added: {text[:50]}...")
    new_message_event.set()


# =============================================================================
# Workers
# =============================================================================

async def generation_worker():
    """Generate audio for pending messages."""
    log.debug("Generation worker started")

    while not shutdown_event.is_set():
        # Wait for messages
        try:
            await asyncio.wait_for(new_message_event.wait(), timeout=0.5)
        except asyncio.TimeoutError:
            continue

        new_message_event.clear()

        while True:
            # Get next message to generate
            async with pending_lock:
                if not pending_messages:
                    break

                if config.queue:
                    # Queue mode: generate oldest first (FIFO)
                    msg = pending_messages[0]
                else:
                    # No-queue mode: generate latest only, drop others
                    while len(pending_messages) > 1:
                        dropped = pending_messages.popleft()
                        log.warning(f"Dropped (generating latest): {dropped.text[:50]}...")
                        play_drop_sound()
                    msg = pending_messages[0]

            log.info(f"Generating: {msg.text[:50]}...")

            # Generate audio (in executor to not block)
            loop = asyncio.get_event_loop()
            audio = await loop.run_in_executor(None, generate_tts, msg.text)

            if audio is None or len(audio) == 0:
                log.warning("Generation produced no audio")
                async with pending_lock:
                    if pending_messages and pending_messages[0].id == msg.id:
                        pending_messages.popleft()
                continue

            # Check if this message is still relevant
            async with pending_lock:
                if not pending_messages or pending_messages[0].id != msg.id:
                    # Message was dropped while generating
                    log.warning(f"Discarded (no longer relevant): {msg.text[:50]}...")
                    continue
                pending_messages.popleft()

            # Save audio and add to ready queue
            audio_file = save_audio(audio)

            async with ready_lock:
                if config.queue:
                    ready_audio.append(ReadyAudio(msg.id, audio_file, msg.text))
                else:
                    # No-queue mode: only keep latest ready
                    while ready_audio:
                        old = ready_audio.popleft()
                        log.warning(f"Dropped ready audio: {old.text[:50]}...")
                        play_drop_sound()
                        try:
                            os.unlink(old.audio_file)
                        except:
                            pass
                    ready_audio.append(ReadyAudio(msg.id, audio_file, msg.text))

            log.info(f"Audio ready: {msg.text[:50]}...")
            audio_ready_event.set()


async def playback_worker():
    """Play ready audio with interrupt handling."""
    global current_process, play_start_time
    log.debug("Playback worker started")

    current_audio_file: Optional[str] = None

    while not shutdown_event.is_set():
        # Check for ready audio
        try:
            await asyncio.wait_for(audio_ready_event.wait(), timeout=0.1)
            audio_ready_event.clear()
        except asyncio.TimeoutError:
            pass

        # Check if current audio finished
        if current_process and current_process.poll() is not None:
            log.debug("Audio end")
            current_process = None
            play_start_time = None
            if current_audio_file:
                try:
                    os.unlink(current_audio_file)
                except:
                    pass
                current_audio_file = None

        # Get next ready audio
        async with ready_lock:
            if not ready_audio:
                continue
            next_audio = ready_audio[0]

        # Handle based on current playback state
        if current_process and current_process.poll() is None:
            # Something is playing
            if not config.interrupt:
                # No interrupt mode: wait for completion
                continue

            # Check minimum duration
            if play_start_time:
                elapsed = time.monotonic() - play_start_time
                if elapsed < config.min_duration:
                    await asyncio.sleep(0.05)
                    continue

            # Interrupt current audio
            log.info("Interrupting")
            stop_current_audio()
            if current_audio_file:
                try:
                    os.unlink(current_audio_file)
                except:
                    pass
                current_audio_file = None

            await play_chime()

        # Pop from ready queue and play
        async with ready_lock:
            if not ready_audio:
                continue
            next_audio = ready_audio.popleft()

        log.info(f"Playing: {next_audio.text[:80]}{'...' if len(next_audio.text) > 80 else ''}")

        player = get_player()
        if player:
            current_process = subprocess.Popen(
                player + [next_audio.audio_file],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            play_start_time = time.monotonic()
            current_audio_file = next_audio.audio_file
            log.debug("Audio start")

    # Cleanup
    stop_current_audio()
    if current_audio_file:
        try:
            os.unlink(current_audio_file)
        except:
            pass


# =============================================================================
# Server
# =============================================================================

async def handle_client(reader, writer):
    """Handle incoming TCP connection."""
    data = b''
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=0.3)
            if not chunk:
                break
            data += chunk
            # Quick ping/pong check
            if data.strip() == b"ping":
                writer.write(b"pong")
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        log.error(f"Read error: {e}")

    try:
        writer.close()
        await writer.wait_closed()
    except:
        pass

    text = data.decode('utf-8', errors='ignore').strip()
    if text and text != "ping":
        await add_message(text)


async def run_server():
    """Run the TCP server."""
    server = await asyncio.start_server(handle_client, 'localhost', config.port)

    addr = server.sockets[0].getsockname()
    log.info(f"Listening on {addr[0]}:{addr[1]}")
    log.info(f"Config: interrupt={config.interrupt}, queue={config.queue}, "
             f"min_duration={config.min_duration}s, max_queue={config.max_queue}")
    log.info("Press Ctrl+C to stop")
    print("", flush=True)

    gen_task = asyncio.create_task(generation_worker())
    play_task = asyncio.create_task(playback_worker())

    try:
        await server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        shutdown_event.set()
        gen_task.cancel()
        play_task.cancel()
        try:
            await gen_task
        except asyncio.CancelledError:
            pass
        try:
            await play_task
        except asyncio.CancelledError:
            pass
        server.close()
        await server.wait_closed()


# =============================================================================
# Main
# =============================================================================

def main():
    global pipeline, config

    parser = argparse.ArgumentParser(description='Kokoro TTS Server')
    parser.add_argument('--port', type=int, default=20202)
    parser.add_argument('--voice', default='af_heart')
    parser.add_argument('--lang', default='a')
    parser.add_argument('--interrupt', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--min-duration', type=float, default=1.5)
    parser.add_argument('--queue', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--max-queue', type=int, default=10)
    parser.add_argument('--interrupt-chime', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--drop-sound', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                        help='Log level (default: INFO)')

    args = parser.parse_args()

    # Setup logging first
    setup_logging(args.log_level)

    config = Config(
        port=args.port,
        voice=args.voice,
        lang=args.lang,
        interrupt=args.interrupt,
        min_duration=args.min_duration,
        queue=args.queue,
        max_queue=args.max_queue,
        interrupt_chime=args.interrupt_chime,
        drop_sound=args.drop_sound,
    )

    log.info("Loading Kokoro model...")
    try:
        from kokoro import KPipeline
        import soundfile as sf
        import numpy as np
    except ImportError as e:
        log.error(f"Import error: {e}")
        log.error("Install with: uv pip install kokoro soundfile numpy")
        sys.exit(1)

    pipeline = KPipeline(lang_code=config.lang, repo_id='hexgrad/Kokoro-82M')
    log.info(f"Model loaded, voice: {config.voice}")

    init_sounds()

    def signal_handler(sig, frame):
        print("")
        log.info("Shutting down...")
        stop_current_audio()
        cleanup_sounds()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        print("")
        log.info("Shutting down...")
        stop_current_audio()
        cleanup_sounds()


if __name__ == '__main__':
    main()

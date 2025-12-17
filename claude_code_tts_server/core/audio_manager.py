"""Audio pipeline with async workers for summarization, TTS generation, and playback."""

import asyncio
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..config import AudioConfig
from ..summarizers.base import SummarizerInterface, SummaryRequest, SummaryType
from ..tts.base import TTSInterface
from .context import get_request_id, sanitize_for_log, set_request_id
from .playback import AudioPlayer
from .sounds import SoundManager, save_audio

log = logging.getLogger("tts-server")


class RequestType(Enum):
    """Type of request for the pipeline."""

    SUMMARIZE = "summarize"
    PERMISSION = "permission"
    SPEAK = "speak"  # Direct TTS, skip summarization


@dataclass
class PendingRequest:
    """A request waiting to be processed."""

    id: str
    request_id: str | None  # For logging correlation
    request_type: RequestType
    content: str
    summary_type: SummaryType | None = None
    metadata: dict | None = None

    @classmethod
    def create(
        cls,
        request_type: RequestType,
        content: str,
        summary_type: SummaryType | None = None,
        metadata: dict | None = None,
    ) -> "PendingRequest":
        """Create a new request with generated ID and current request context."""
        return cls(
            id=str(uuid.uuid4()),
            request_id=get_request_id(),
            request_type=request_type,
            content=content,
            summary_type=summary_type,
            metadata=metadata,
        )


@dataclass
class PendingMessage:
    """A message waiting to be converted to speech."""

    id: str
    request_id: str | None
    text: str
    timestamp: float

    @classmethod
    def create(cls, text: str, request_id: str | None = None) -> "PendingMessage":
        """Create a new message with generated ID and current timestamp."""
        return cls(
            id=str(uuid.uuid4()),
            request_id=request_id,
            text=text,
            timestamp=time.time(),
        )


@dataclass
class ReadyAudio:
    """Audio that has been generated and is ready to play."""

    id: str
    request_id: str | None
    audio_file: Path
    text: str


@dataclass
class QueueStatus:
    """Status of the audio pipeline."""

    pending_requests: int
    pending_messages: int
    ready_audio: int
    is_playing: bool
    current_text: str | None = None


class AudioPipeline:
    """Manages the full audio pipeline: summarization → TTS → playback."""

    def __init__(
        self,
        config: AudioConfig,
        tts: TTSInterface,
        summarizer: SummarizerInterface,
    ):
        self.config = config
        self.tts = tts
        self.summarizer = summarizer

        # Pipeline queues
        self.pending_requests: deque[PendingRequest] = deque()
        self.pending_messages: deque[PendingMessage] = deque()
        self.ready_audio: deque[ReadyAudio] = deque()

        # Locks for queue access
        self.requests_lock = asyncio.Lock()
        self.messages_lock = asyncio.Lock()
        self.audio_lock = asyncio.Lock()

        # Playback
        self.player = AudioPlayer()
        self.sounds = SoundManager(tts.get_sample_rate())
        self._current_text: str | None = None

        # Control events
        self.shutdown_event = asyncio.Event()
        self.new_request_event = asyncio.Event()
        self.new_message_event = asyncio.Event()
        self.audio_ready_event = asyncio.Event()

        # Worker tasks
        self._summarizer_task: asyncio.Task | None = None
        self._generator_task: asyncio.Task | None = None
        self._playback_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the worker tasks."""
        self.sounds.init_sounds()
        self._summarizer_task = asyncio.create_task(self._summarizer_worker())
        self._generator_task = asyncio.create_task(self._generator_worker())
        self._playback_task = asyncio.create_task(self._playback_worker())
        log.debug("Audio pipeline started")

    async def stop(self) -> None:
        """Stop workers and cleanup."""
        self.shutdown_event.set()

        # Cancel tasks
        for task in [self._summarizer_task, self._generator_task, self._playback_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Stop playback and cleanup
        self.player.stop()
        self.sounds.cleanup()

        # Clean up any remaining audio files
        async with self.audio_lock:
            for audio in self.ready_audio:
                try:
                    os.unlink(audio.audio_file)
                except OSError:
                    pass
            self.ready_audio.clear()

        log.debug("Audio pipeline stopped")

    async def add_request(
        self,
        request_type: RequestType,
        content: str,
        summary_type: SummaryType | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Add a new request to the pipeline.

        Args:
            request_type: Type of request (SUMMARIZE, PERMISSION, SPEAK).
            content: Content to process.
            summary_type: Type of summarization (for SUMMARIZE/PERMISSION).
            metadata: Additional metadata for processing.

        Returns:
            The request ID.
        """
        req = PendingRequest.create(request_type, content, summary_type, metadata)

        async with self.requests_lock:
            if self.config.queue:
                # Queue mode: add to queue, drop oldest if over limit
                while len(self.pending_requests) >= self.config.max_queue:
                    dropped = self.pending_requests.popleft()
                    log.warning(f"Queue full, dropped request: {sanitize_for_log(dropped.content, 50)}")
                    self._play_drop_sound()
                self.pending_requests.append(req)
            else:
                # No-queue mode: replace all pending with latest
                while self.pending_requests:
                    dropped = self.pending_requests.popleft()
                    log.warning(f"Dropped request (latest-only): {sanitize_for_log(dropped.content, 50)}")
                    self._play_drop_sound()
                self.pending_requests.append(req)

        self.new_request_event.set()
        return req.id

    async def add_message(self, text: str) -> str:
        """Add a message directly to TTS queue (skip summarization).

        Args:
            text: Text to convert to speech.

        Returns:
            The message ID.
        """
        msg = PendingMessage.create(text, request_id=get_request_id())

        async with self.messages_lock:
            if self.config.queue:
                while len(self.pending_messages) >= self.config.max_queue:
                    dropped = self.pending_messages.popleft()
                    log.warning(f"Queue full, dropped message: {sanitize_for_log(dropped.text, 50)}")
                    self._play_drop_sound()
                self.pending_messages.append(msg)
            else:
                while self.pending_messages:
                    dropped = self.pending_messages.popleft()
                    log.warning(f"Dropped message (latest-only): {sanitize_for_log(dropped.text, 50)}")
                    self._play_drop_sound()
                self.pending_messages.append(msg)

        self.new_message_event.set()
        return msg.id

    def get_status(self) -> QueueStatus:
        """Get current pipeline status."""
        return QueueStatus(
            pending_requests=len(self.pending_requests),
            pending_messages=len(self.pending_messages),
            ready_audio=len(self.ready_audio),
            is_playing=self.player.is_playing(),
            current_text=self._current_text,
        )

    async def clear_queue(self) -> int:
        """Clear all pending items in the pipeline.

        Returns:
            Number of items cleared.
        """
        count = 0

        async with self.requests_lock:
            count += len(self.pending_requests)
            self.pending_requests.clear()

        async with self.messages_lock:
            count += len(self.pending_messages)
            self.pending_messages.clear()

        async with self.audio_lock:
            for audio in self.ready_audio:
                try:
                    os.unlink(audio.audio_file)
                except OSError:
                    pass
            count += len(self.ready_audio)
            self.ready_audio.clear()

        log.debug(f"Cleared {count} items from pipeline")
        return count

    async def skip_current(self) -> bool:
        """Skip the currently playing audio.

        Returns:
            True if something was skipped, False otherwise.
        """
        if self.player.is_playing():
            audio_file = self.player.stop()
            if audio_file:
                try:
                    os.unlink(audio_file)
                except OSError:
                    pass
            self._current_text = None
            log.debug("Skipped current audio")
            return True
        return False

    def _play_drop_sound(self) -> None:
        """Play drop sound if enabled."""
        if self.config.drop_sound:
            self.player.play_drop_sound(self.sounds.drop_file)

    async def _summarizer_worker(self) -> None:
        """Process requests: summarize and push to message queue."""
        log.debug("Summarizer worker started")

        while not self.shutdown_event.is_set():
            # Wait for requests
            try:
                await asyncio.wait_for(self.new_request_event.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            self.new_request_event.clear()

            while True:
                # Get next request
                async with self.requests_lock:
                    if not self.pending_requests:
                        break

                    if self.config.queue:
                        req = self.pending_requests[0]
                    else:
                        # No-queue mode: process latest only
                        while len(self.pending_requests) > 1:
                            dropped = self.pending_requests.popleft()
                            log.warning(f"Dropped request (processing latest): {sanitize_for_log(dropped.content, 50)}")
                            self._play_drop_sound()
                        req = self.pending_requests[0]

                # Set request ID for logging
                if req.request_id:
                    set_request_id(req.request_id)

                # Process based on request type
                if req.request_type == RequestType.SPEAK:
                    # Direct TTS - no summarization needed
                    text = req.content
                    log.debug(f"Speak request ({len(text)} chars)")
                else:
                    # Summarize the content
                    log.debug(f"Summarization start ({len(req.content)} chars)")
                    try:
                        result = await self.summarizer.summarize(
                            SummaryRequest(
                                content=req.content,
                                summary_type=req.summary_type or SummaryType.SHORT_RESPONSE,
                                metadata=req.metadata,
                            )
                        )
                        text = result.text
                        log.debug(f"Summarization end ({len(text)} chars): {sanitize_for_log(text)}")
                    except Exception as e:
                        log.error(f"Summarization failed: {e}")
                        # Remove failed request and continue
                        async with self.requests_lock:
                            if self.pending_requests and self.pending_requests[0].id == req.id:
                                self.pending_requests.popleft()
                        continue

                # Check if request is still relevant
                async with self.requests_lock:
                    if not self.pending_requests or self.pending_requests[0].id != req.id:
                        log.warning(f"Discarded (no longer relevant): {sanitize_for_log(req.content, 50)}")
                        continue
                    self.pending_requests.popleft()

                # Push to message queue
                msg = PendingMessage.create(text, request_id=req.request_id)

                async with self.messages_lock:
                    if self.config.queue:
                        self.pending_messages.append(msg)
                    else:
                        while self.pending_messages:
                            dropped = self.pending_messages.popleft()
                            log.warning(f"Dropped message (latest-only): {sanitize_for_log(dropped.text, 50)}")
                            self._play_drop_sound()
                        self.pending_messages.append(msg)

                self.new_message_event.set()

    async def _generator_worker(self) -> None:
        """Generate audio for pending messages."""
        log.debug("Generator worker started")

        while not self.shutdown_event.is_set():
            # Wait for messages
            try:
                await asyncio.wait_for(self.new_message_event.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            self.new_message_event.clear()

            while True:
                # Get next message
                async with self.messages_lock:
                    if not self.pending_messages:
                        break

                    if self.config.queue:
                        msg = self.pending_messages[0]
                    else:
                        while len(self.pending_messages) > 1:
                            dropped = self.pending_messages.popleft()
                            log.warning(f"Dropped message (generating latest): {sanitize_for_log(dropped.text, 50)}")
                            self._play_drop_sound()
                        msg = self.pending_messages[0]

                # Set request ID for logging
                if msg.request_id:
                    set_request_id(msg.request_id)

                log.debug(f"Audio generation start ({len(msg.text)} chars): {sanitize_for_log(msg.text)}")

                # Generate audio
                audio = await self.tts.synthesize(msg.text)

                if audio is None or len(audio) == 0:
                    log.warning("Generation produced no audio")
                    async with self.messages_lock:
                        if self.pending_messages and self.pending_messages[0].id == msg.id:
                            self.pending_messages.popleft()
                    continue

                # Check if message is still relevant
                async with self.messages_lock:
                    if not self.pending_messages or self.pending_messages[0].id != msg.id:
                        log.warning(f"Discarded (no longer relevant): {sanitize_for_log(msg.text, 50)}")
                        continue
                    self.pending_messages.popleft()

                # Save audio and add to ready queue
                audio_file = save_audio(audio, self.tts.get_sample_rate(), self.config.speed)

                async with self.audio_lock:
                    ready = ReadyAudio(msg.id, msg.request_id, audio_file, msg.text)
                    if self.config.queue:
                        self.ready_audio.append(ready)
                    else:
                        while self.ready_audio:
                            old = self.ready_audio.popleft()
                            log.warning(f"Dropped ready audio: {sanitize_for_log(old.text, 50)}")
                            self._play_drop_sound()
                            try:
                                os.unlink(old.audio_file)
                            except OSError:
                                pass
                        self.ready_audio.append(ready)

                log.debug(f"Audio generation end: {sanitize_for_log(msg.text, 50)}")
                self.audio_ready_event.set()

    async def _playback_worker(self) -> None:
        """Play ready audio with interrupt handling."""
        log.debug("Playback worker started")

        while not self.shutdown_event.is_set():
            # Check for ready audio
            try:
                await asyncio.wait_for(self.audio_ready_event.wait(), timeout=0.1)
                self.audio_ready_event.clear()
            except asyncio.TimeoutError:
                pass

            # Check if current audio finished
            finished_file = self.player.check_finished()
            if finished_file:
                log.debug("Audio end")
                self._current_text = None
                try:
                    os.unlink(finished_file)
                except OSError:
                    pass

            # Get next ready audio
            async with self.audio_lock:
                if not self.ready_audio:
                    continue
                next_audio = self.ready_audio[0]

            # Handle based on current playback state
            if self.player.is_playing():
                if not self.config.interrupt:
                    continue

                elapsed = self.player.get_elapsed_time()
                if elapsed is not None and elapsed < self.config.min_duration:
                    await asyncio.sleep(0.05)
                    continue

                # Interrupt current audio
                log.debug("Interrupting current audio")
                audio_file = self.player.stop()
                self._current_text = None
                if audio_file:
                    try:
                        os.unlink(audio_file)
                    except OSError:
                        pass

                if self.config.interrupt_chime:
                    await self.player.play_chime(self.sounds.chime_file)

            # Pop from ready queue and play
            async with self.audio_lock:
                if not self.ready_audio:
                    continue
                next_audio = self.ready_audio.popleft()

            # Set request ID for logging
            if next_audio.request_id:
                set_request_id(next_audio.request_id)

            log.info(f"Playing: {sanitize_for_log(next_audio.text)}")

            if self.player.play(next_audio.audio_file):
                self._current_text = next_audio.text
                log.debug("Audio start")

        # Cleanup on shutdown
        audio_file = self.player.stop()
        if audio_file:
            try:
                os.unlink(audio_file)
            except OSError:
                pass


# Backwards compatibility alias
AudioManager = AudioPipeline

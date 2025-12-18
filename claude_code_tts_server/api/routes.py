"""FastAPI route definitions."""

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from ..core.audio_manager import AudioPipeline, RequestType
from ..core.transcript import parse_transcript
from ..summarizers.base import SummaryType
from .models import (
    HealthResponse,
    MessageResponse,
    PermissionRequest,
    QueueStatusResponse,
    SpeakRequest,
    SummarizeRequest,
)

log = logging.getLogger("tts-server")

router = APIRouter()


def get_pipeline(request: Request) -> AudioPipeline:
    """Get AudioPipeline from app state."""
    return request.app.state.audio_manager


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Health check endpoint."""
    pipeline = get_pipeline(request)
    summarizer = request.app.state.summarizer

    status = pipeline.get_status()
    summarizer_ready = await summarizer.health_check()

    return HealthResponse(
        status="ok",
        tts_ready=True,
        summarizer_ready=summarizer_ready,
        queue_depth=status.pending_requests + status.pending_messages + status.ready_audio,
    )


@router.post("/summarize", response_model=MessageResponse)
async def summarize(
    request: Request,
    body: SummarizeRequest,
) -> MessageResponse:
    """Full summarization pipeline for Stop hook.

    Parses transcript and queues for summarization → TTS → playback.
    Returns immediately after queuing.
    """
    pipeline = get_pipeline(request)

    # Parse transcript content
    if not body.transcript_content:
        raise HTTPException(status_code=400, detail="transcript_content is required")

    parsed = parse_transcript(body.transcript_content)
    if not parsed:
        raise HTTPException(status_code=400, detail="No content in transcript")

    content = parsed.content
    has_tool_calls = parsed.has_tool_calls
    content_length = parsed.length
    if parsed.truncated:
        log.debug(f"Content truncated to {content_length} chars")

    log.info(f"POST /summarize ({content_length} chars)")

    # Determine summary type
    if has_tool_calls or content_length >= 300:
        summary_type = SummaryType.LONG_RESPONSE
    else:
        summary_type = SummaryType.SHORT_RESPONSE

    # Queue for processing (returns immediately)
    request_id = await pipeline.add_request(
        request_type=RequestType.SUMMARIZE,
        content=content,
        summary_type=summary_type,
    )

    return MessageResponse(
        message_id=request_id,
        status="queued",
    )


@router.post("/permission", response_model=MessageResponse)
async def permission(
    request: Request,
    body: PermissionRequest,
) -> MessageResponse:
    """Permission announcement pipeline for PermissionRequest hook.

    Queues for summarization → TTS → playback.
    Returns immediately after queuing.
    """
    pipeline = get_pipeline(request)

    log.info(f"POST /permission tool={body.tool_name}")

    # Build description for summarization
    tool_input_str = json.dumps(body.tool_input)
    description = body.tool_input.get("description", "")

    if description:
        content = f"Tool: {body.tool_name}. Description: {description}. Input: {tool_input_str}"
    else:
        content = f"Tool: {body.tool_name}. Input: {tool_input_str}"

    # Queue for processing (returns immediately)
    request_id = await pipeline.add_request(
        request_type=RequestType.PERMISSION,
        content=content,
        summary_type=SummaryType.PERMISSION_REQUEST,
        metadata={"tool_name": body.tool_name},
    )

    return MessageResponse(
        message_id=request_id,
        status="queued",
    )


@router.post("/speak", response_model=MessageResponse)
async def speak(
    request: Request,
    body: SpeakRequest,
) -> MessageResponse:
    """Direct TTS - skip summarization, just speak the text."""
    pipeline = get_pipeline(request)

    log.info(f"POST /speak ({len(body.text)} chars)")

    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    # Queue directly for TTS (skip summarization)
    message_id = await pipeline.add_message(body.text)

    return MessageResponse(
        message_id=message_id,
        status="queued",
    )


@router.get("/queue", response_model=QueueStatusResponse)
async def get_queue_status(request: Request) -> QueueStatusResponse:
    """Get current queue status."""
    pipeline = get_pipeline(request)
    status = pipeline.get_status()

    return QueueStatusResponse(
        pending_requests=status.pending_requests,
        pending_messages=status.pending_messages,
        ready_audio=status.ready_audio,
        is_playing=status.is_playing,
        current_text=status.current_text,
    )


@router.post("/queue/clear")
async def clear_queue(request: Request) -> dict:
    """Clear all pending and ready audio."""
    pipeline = get_pipeline(request)
    count = await pipeline.clear_queue()

    return {"cleared": count, "status": "ok"}


@router.post("/queue/skip")
async def skip_current(request: Request) -> dict:
    """Skip currently playing audio."""
    pipeline = get_pipeline(request)
    skipped = await pipeline.skip_current()

    return {"skipped": skipped, "status": "ok"}

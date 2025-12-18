"""Transcript parsing for Claude Code JSONL transcripts."""

import json
import re
from dataclasses import dataclass

# Default max content length (~5k tokens worth)
DEFAULT_MAX_CONTENT_LENGTH = 20000


@dataclass
class ParsedTranscript:
    """Result of parsing a transcript."""

    content: str
    has_tool_calls: bool
    length: int
    truncated: bool = False


def parse_transcript(
    content: str,
    max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,
) -> ParsedTranscript | None:
    """Parse Claude Code transcript JSONL content.

    Extracts assistant content since the last user message or interrupt.
    This mirrors the jq logic from the shell scripts.

    Args:
        content: JSONL content string (newline-separated JSON objects).
        max_content_length: Maximum content length before truncation (default 20000).

    Returns:
        ParsedTranscript with content and metadata, or None if no content.
    """
    if not content or not content.strip():
        return None

    # Parse JSONL content
    entries = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not entries:
        return None

    # Find boundary: last real user message OR last interrupt
    # An interrupt is when user rejects a tool use
    last_user_idx = -1
    last_interrupt_idx = -1

    for i, entry in enumerate(entries):
        if entry.get("type") != "user":
            continue

        content = entry.get("message", {}).get("content", [])

        # Handle string content (from context summarization) - treat as real user message
        if isinstance(content, str):
            last_user_idx = i
            continue

        if not isinstance(content, list):
            continue

        # Check if this is a tool_result message (not a real user message)
        has_tool_result = any(
            isinstance(c, dict) and c.get("type") == "tool_result"
            for c in content
        )

        if not has_tool_result:
            last_user_idx = i

        # Check for interrupt (rejected tool use)
        for c in content:
            if not isinstance(c, dict) or c.get("type") != "tool_result":
                continue
            result_content = c.get("content", "")
            if isinstance(result_content, str):
                if re.search(
                    r"The user doesn.t want to proceed|tool use was rejected",
                    result_content,
                    re.IGNORECASE,
                ):
                    last_interrupt_idx = i
                    break

    # Use the more recent boundary
    boundary_idx = max(last_user_idx, last_interrupt_idx)

    # Collect assistant content after the boundary
    content_parts = []
    has_tool_calls = False

    for entry in entries[boundary_idx + 1:]:
        if entry.get("type") != "assistant":
            continue

        content = entry.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue

        for item in content:
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")

            if item_type == "text":
                text = item.get("text", "")
                if text:
                    content_parts.append(text)

            elif item_type == "tool_use":
                has_tool_calls = True
                tool_name = item.get("name", "unknown")
                tool_input = item.get("input", {})

                # Format tool call with truncated values
                params = []
                for k, v in tool_input.items():
                    v_str = str(v) if not isinstance(v, str) else v
                    if len(v_str) > 150:
                        v_str = v_str[:150] + "..."
                    params.append(f"{k}: {v_str}")

                tool_str = f"[Tool: {tool_name}] {', '.join(params)}"
                content_parts.append(tool_str)

    if not content_parts:
        return None

    full_content = "\n\n".join(content_parts)

    # Truncate from the beginning if content is too long (keep most recent)
    truncated = False
    if len(full_content) > max_content_length:
        full_content = "[Earlier content truncated...]\n\n" + full_content[-max_content_length:]
        truncated = True

    return ParsedTranscript(
        content=full_content,
        has_tool_calls=has_tool_calls,
        length=len(full_content),
        truncated=truncated,
    )

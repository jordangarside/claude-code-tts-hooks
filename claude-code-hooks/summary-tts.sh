#!/bin/bash

# Claude Code TTS Summary Hook
# Posts transcript to TTS server for summarization and speech
#
# This is a thin wrapper that forwards the Stop hook payload to the TTS server.
# All summarization logic is handled by the server.
#
# Optional environment variables:
#   SUMMARY_AUDIO_PORT - Port for TTS server (default: 20202)

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_NAME="$(basename "$0" .sh)"
LOG_FILE="$SCRIPT_DIR/$SCRIPT_NAME.output"

TTS_PORT="${SUMMARY_AUDIO_PORT:-20202}"
TTS_URL="http://localhost:${TTS_PORT}"

# Log to file
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Guard: This script is meant to be called by Claude Code hooks, not directly
if [ -t 0 ]; then
  echo "Error: This script is a Claude Code hook and should not be run directly." >&2
  echo "" >&2
  echo "It expects JSON input on stdin from Claude Code's hook system." >&2
  echo "Configure it in .claude/settings.local.json as a 'Stop' hook." >&2
  exit 1
fi

# Read hook input from stdin
input=$(cat)
transcript_path=$(echo "$input" | jq -r '.transcript_path // empty')

# Exit silently if no transcript
if [ -z "$transcript_path" ] || [ ! -f "$transcript_path" ]; then
  exit 0
fi

# Save transcript for debugging
cp "$transcript_path" "$SCRIPT_DIR/$SCRIPT_NAME.transcript" 2>/dev/null

# POST to TTS server (send content, not path - supports remote servers)
# Take last 100KB of transcript - server truncates parsed content to 20KB anyway
# Pipe JSON via stdin to avoid "Argument list too long" for large transcripts
response=$(tail -c 100000 "$transcript_path" | \
  jq -Rs '{transcript_content: .}' | \
  curl -s -X POST "${TTS_URL}/summarize" \
    -H "Content-Type: application/json" \
    -d @- \
    --max-time 30 2>&1)

exit_code=$?

if [ $exit_code -ne 0 ]; then
  log "Failed to reach TTS server: curl exit code $exit_code"
  log "Response: $response"
  echo "Failed to reach TTS server: curl exit code $exit_code" >&2
  [ -n "$response" ] && echo "Response: $response" >&2
  exit 1
fi

# Check for error in response
error=$(echo "$response" | jq -r '.error // empty' 2>/dev/null)
if [ -n "$error" ]; then
  log "TTS server error: $error"
  detail=$(echo "$response" | jq -r '.detail // empty' 2>/dev/null)
  [ -n "$detail" ] && log "Detail: $detail"
  echo "TTS server error: $error" >&2
  [ -n "$detail" ] && echo "Detail: $detail" >&2
  exit 1
fi

# Log success
message_id=$(echo "$response" | jq -r '.message_id // empty' 2>/dev/null)
log "Message queued: $message_id"

exit 0

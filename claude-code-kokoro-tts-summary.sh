#!/bin/bash

# Claude Code Audio Summary Hook
# Summarizes assistant responses and sends them to Kokoro TTS server
#
# Required environment variables:
#   SUMMARY_GROQ_API_KEY        - Groq API key for summarization
#
# Optional environment variables:
#   SUMMARY_GROQ_MODEL_LARGE    - Groq model for summarization (default: openai/gpt-oss-120b)
#   SUMMARY_GROQ_MODEL_SMALL    - Groq model for short responses (default: llama-3.1-8b-instant)
#   SUMMARY_AUDIO_PORT          - Port for Kokoro TTS server (default: 20202)
#
# Requires kokoro-server.py running locally:
#   ./kokoro-server.py --port 20202

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_NAME="$(basename "$0" .sh)"
ERROR_LOG="$SCRIPT_DIR/$SCRIPT_NAME.output"

# Clear previous error log
rm -f "$ERROR_LOG"

# Log error to both stderr and file (overwrites file)
log_error() {
  echo "[kokoro-tts-summary] $1" >&2
  echo "[$(date)] $1" > "$ERROR_LOG"
}

# Log success with original text and summary
log_success() {
  local original="$1"
  local summary="$2"
  {
    echo "=== $(date) ==="
    echo ""
    echo "--- Original ---"
    echo "$original" | jq -r '.'
    echo ""
    echo "--- Summary ---"
    echo "$summary"
  } > "$ERROR_LOG"
}

AUDIO_PORT="${SUMMARY_AUDIO_PORT:-20202}"

# Detect nc flavor (GNU needs -q flag to quit after EOF, BSD doesn't)
# Use 1 second timeout (minimum for nc) to fail fast if server is down
NC_SEND_FLAGS="-w 1"
if nc -h 2>&1 | grep -q '\-q'; then
  NC_SEND_FLAGS="-q 1 -w 1"
fi

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
transcript_path=$(echo "$input" | jq -r '.transcript_path')

if [ ! -f "$transcript_path" ]; then
  exit 0
fi

# Save raw transcript for debugging
cp "$transcript_path" "$SCRIPT_DIR/$SCRIPT_NAME.transcript"

# Extract the latest assistant response from transcript (JSONL format)
# A Claude "turn" spans multiple API messages (tool calls, tool results, final response)
# We get all assistant content since the last real user message (not tool_result)
latest_response=$(jq -s '
  # Helper: check if content array contains tool_result
  def has_tool_result: if type == "array" then any(.type == "tool_result") else false end;
  # Find index of last real user message (not a tool_result)
  (to_entries | map(select(.value.type == "user" and (.value.message.content | has_tool_result | not))) | last | .key // -1) as $last_user_idx |
  # Get all assistant entries after that index
  to_entries | map(select(.key > $last_user_idx and .value.type == "assistant")) | map(.value) |
  # Collect all content items (handle both array and non-array content)
  [.[].message.content | if type == "array" then .[] else empty end] |
  # Process each content item
  map(
    if .type == "text" then
      .text
    elif .type == "tool_use" then
      "[Tool: \(.name)] " + (
        .input | to_entries | map(
          "\(.key): \(.value |
            if type == "string" then
              (if (. | length) > 150 then .[0:150] + "..." else . end)
            else
              (tostring | if (. | length) > 150 then .[0:150] + "..." else . end)
            end
          )"
        ) | join(", ")
      )
    else
      empty
    end
  ) | join("\n\n")
' "$transcript_path" 2>/dev/null)

if [ -z "$latest_response" ]; then
  exit 0
fi

# Check required env vars
if [ -z "$SUMMARY_GROQ_API_KEY" ]; then
  log_error "SUMMARY_GROQ_API_KEY not set"
  exit 1
fi

# Check if Kokoro server is actually reachable (not just port open)
# This handles SSH reverse tunnels where the port is open but the server isn't running locally
check_kokoro_server() {
  # Send ping and expect pong response
  # Use timeout for sub-second timeout (0.2s), fall back to nc's 1s minimum
  local response
  if command -v timeout >/dev/null 2>&1; then
    response=$(echo "ping" | timeout 0.2 nc localhost "$AUDIO_PORT" 2>/dev/null)
  else
    response=$(echo "ping" | nc $NC_SEND_FLAGS localhost "$AUDIO_PORT" 2>/dev/null)
  fi
  [ "$response" = "pong" ]
}

if ! check_kokoro_server; then
  log_error "Kokoro server not responding on port $AUDIO_PORT. Start with: uv run kokoro-server.py --port $AUDIO_PORT"
  exit 1
fi

# Choose prompt and model based on response content
response_length=${#latest_response}
has_tool_calls=false
if echo "$latest_response" | grep -q '\[Tool:'; then
  has_tool_calls=true
fi

if [ "$has_tool_calls" = false ] && [ "$response_length" -lt 300 ]; then
  # Short text-only responses: just strip formatting for TTS (use smaller/faster model)
  GROQ_MODEL="${SUMMARY_GROQ_MODEL_SMALL:-llama-3.1-8b-instant}"
  SYSTEM_PROMPT="Convert this text for text-to-speech by removing markdown formatting and code blocks. Expand abbreviated units (0.2s -> 0.2 seconds, 100ms -> 100 milliseconds, 5MB -> 5 megabytes). Expand ALL file extensions to full names (.py -> Python, .js -> JavaScript, .yaml -> YAML, .html -> HTML). Output ONLY the cleaned text."
else
  # Long responses or responses with tool calls: summarize for TTS
  GROQ_MODEL="${SUMMARY_GROQ_MODEL_LARGE:-openai/gpt-oss-120b}"
  SYSTEM_PROMPT="Summarize the following Claude Code response for text-to-speech. Write 1-3 sentences in first-person AS IF YOU ARE Claude Code.

Rules:
- ACTIONS (edited files, ran commands): use past tense. Example: I updated the config and ran the tests.
- CREATIVE CONTENT (stories, poems, jokes you wrote): summarize what was created. Example: I told a story about a clockmaker who discovers a mysterious automaton.
- EXPLANATIONS: summarize what was explained. Example: I explained how the authentication system works.
- QUESTIONS: keep as-is. Example: How would you like to proceed?
- No markdown, no bullet points, no code blocks
- Expand abbreviated units (0.2s -> 0.2 seconds, 100ms -> 100 milliseconds, 5MB -> 5 megabytes)
- Expand ALL file extensions to full names (.py -> Python, .js -> JavaScript, .yaml -> YAML, .html -> HTML)
- Output ONLY the first-person summary"
fi

groq_response=$(curl -s -X POST "https://api.groq.com/openai/v1/chat/completions" \
    -H "Authorization: Bearer $SUMMARY_GROQ_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg system "$SYSTEM_PROMPT" --arg content "$latest_response" --arg model "$GROQ_MODEL" '{
      model: $model,
      messages: [
        {role: "system", content: $system},
        {role: "user", content: $content}
      ],
      temperature: 0.3,
      max_tokens: 2048
    }')")

if [ $? -ne 0 ]; then
  log_error "Groq API call failed"
  exit 1
fi

# Check for Groq API error
groq_error=$(echo "$groq_response" | jq -r '.error.message // empty')
if [ -n "$groq_error" ]; then
  log_error "Groq API error: $groq_error"
  exit 1
fi

summary=$(echo "$groq_response" | jq -r '.choices[0].message.content // empty')

if [ -z "$summary" ]; then
  log_error "Groq returned empty summary"
  exit 1
fi

# Send text to Kokoro TTS server
# Works for both SSH (via reverse tunnel) and local (direct to localhost)
send_to_kokoro() {
  # Redirect both stdout and stderr (netcat-openbsd prints errors to stdout)
  if echo "$summary" | nc $NC_SEND_FLAGS localhost "$AUDIO_PORT" >/dev/null 2>&1; then
    return 0
  else
    log_error "Failed to send to Kokoro server. Ensure kokoro-server.py is running on port $AUDIO_PORT"
    return 1
  fi
}

# Log original and summary before sending to TTS
log_success "$latest_response" "$summary"

if ! send_to_kokoro; then
  exit 1
fi

exit 0

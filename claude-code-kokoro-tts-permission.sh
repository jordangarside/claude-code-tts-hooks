#!/bin/bash

# Claude Code Permission Request Hook
# Announces permission requests via TTS using Groq for summarization
#
# Required environment variables:
#   SUMMARY_GROQ_API_KEY        - Groq API key for summarization
#
# Optional environment variables:
#   SUMMARY_GROQ_MODEL_SMALL    - Groq model (default: llama-3.1-8b-instant)
#   SUMMARY_AUDIO_PORT          - Port for Kokoro TTS server (default: 20202)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_NAME="$(basename "$0" .sh)"
ERROR_LOG="$SCRIPT_DIR/$SCRIPT_NAME.output"

AUDIO_PORT="${SUMMARY_AUDIO_PORT:-20202}"
GROQ_MODEL="${SUMMARY_GROQ_MODEL_SMALL:-llama-3.1-8b-instant}"

# Clear previous log
rm -f "$ERROR_LOG"

# Log success with input and output
log_result() {
  local input="$1"
  local description="$2"
  local output="$3"
  {
    echo "=== $(date) ==="
    echo ""
    echo "--- Input ---"
    echo "$input" | jq '.' 2>/dev/null || echo "$input"
    echo ""
    echo "--- Prompt Description ---"
    echo "$description"
    echo ""
    echo "--- Output ---"
    echo "$output"
  } > "$ERROR_LOG"
}

# Guard: This script is meant to be called by Claude Code hooks, not directly
if [ -t 0 ]; then
  echo "Error: This script is a Claude Code hook and should not be run directly." >&2
  exit 1
fi

# Read hook input from stdin
input=$(cat)

# Extract tool info
tool_name=$(echo "$input" | jq -r '.tool_name // empty')
tool_input=$(echo "$input" | jq -r '.tool_input // empty')

if [ -z "$tool_name" ]; then
  exit 0
fi

# Check required env vars
if [ -z "$SUMMARY_GROQ_API_KEY" ]; then
  exit 0  # Silent exit - don't block permission dialog
fi

# Quick check if TTS server is available (non-blocking)
if ! (echo >/dev/tcp/localhost/"$AUDIO_PORT") 2>/dev/null; then
  exit 0
fi

# Build description for Groq - include Claude's description if present
claude_description=$(echo "$tool_input" | jq -r '.description // empty')
if [ -n "$claude_description" ]; then
  prompt_description="Tool: $tool_name. Description: $claude_description. Input: $tool_input"
else
  prompt_description="Tool: $tool_name. Input: $tool_input"
fi

# Summarize for TTS using cheap model
SYSTEM_PROMPT="Convert this permission request into a brief spoken announcement (under 30 words). Start with 'Permission requested:'. No quotes, no special characters. Output ONLY the announcement.

Examples:
Input: Tool: Bash. Description: Install dependencies. Input: {\"command\":\"npm install\",\"description\":\"Install dependencies\"}
Output: Permission requested: Command to install node dependencies

Input: Tool: WebFetch. Input: {\"url\":\"https://docs.python.org/3/library/json.html\",\"prompt\":\"How do I parse JSON?\"}
Output: Permission requested: Fetch Python documentation page

Input: Tool: Edit. Input: {\"file_path\":\"/src/auth.js\",\"old_string\":\"token\",\"new_string\":\"sessionToken\"}
Output: Permission requested: Edit auth.js file

Input: Tool: Bash. Description: Show working tree status. Input: {\"command\":\"git status\",\"description\":\"Show working tree status\"}
Output: Permission requested: Command to show working tree status

Input: Tool: Bash. Input: {\"command\":\"docker ps -a\"}
Output: Permission requested: Command to list all Docker containers"

groq_response=$(curl -s --max-time 5 -X POST "https://api.groq.com/openai/v1/chat/completions" \
    -H "Authorization: Bearer $SUMMARY_GROQ_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg system "$SYSTEM_PROMPT" --arg content "$prompt_description" --arg model "$GROQ_MODEL" '{
      model: $model,
      messages: [
        {role: "system", content: $system},
        {role: "user", content: $content}
      ],
      temperature: 0.1,
      max_tokens: 50
    }')")

if [ $? -ne 0 ]; then
  exit 0
fi

summary=$(echo "$groq_response" | jq -r '.choices[0].message.content // empty')

if [ -z "$summary" ]; then
  # Fallback to simple announcement
  summary="Permission requested for $tool_name"
fi

# Log input and output
log_result "$input" "$prompt_description" "$summary"

# Send to TTS server (non-blocking, fire and forget)
echo "$summary" | nc -w 1 localhost "$AUDIO_PORT" >/dev/null 2>&1 &

exit 0

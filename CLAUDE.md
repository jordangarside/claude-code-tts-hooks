# CLAUDE.md

## Project Overview

Claude Code TTS Hooks - Audio feedback for Claude Code via text-to-speech.

**Hooks:**
- **Stop** - Summarizes Claude's response and plays it via TTS
- **PermissionRequest** - Announces what permission Claude is requesting

## Architecture

```
claude-code-kokoro-tts-summary.sh    # Stop hook - summarizes responses
claude-code-kokoro-tts-permission.sh # PermissionRequest hook - announces permissions
kokoro-server.py                     # Async TTS server (Kokoro-82M)
```

**Hook Flow:**
1. Claude Code triggers hook with JSON on stdin
2. Hook extracts relevant info (transcript or tool request)
3. Groq API summarizes/formats for speech
4. Text sent to TTS server via TCP socket
5. Server generates and plays audio

**Server Features:**
- Async - handles ping/pong health checks instantly, even while playing
- Background generation - prepares next audio while current plays
- Interrupt - only when new audio is ready (no silence gaps)
- Chime - two-note G5→C6 transition sound on interrupt
- Rapid batching - plays ~0.8s snippets of queued messages

## Environment Variables

| Variable | Required | Default |
|----------|----------|---------|
| `SUMMARY_GROQ_API_KEY` | Yes | - |
| `SUMMARY_GROQ_MODEL_LARGE` | No | `openai/gpt-oss-120b` |
| `SUMMARY_GROQ_MODEL_SMALL` | No | `llama-3.1-8b-instant` |
| `SUMMARY_AUDIO_PORT` | No | `20202` |

## Running

```bash
# Start TTS server
uv run kokoro-server.py --port 20202

# For remote usage, SSH with reverse tunnel
ssh -R 20202:localhost:20202 user@server
```

## Debugging

Output logs:
- `claude-code-kokoro-tts-summary.output`
- `claude-code-kokoro-tts-permission.output`

## Commit Messages

Do not include the "Generated with Claude Code" line in commit messages.

Commits should still include the `Co-Authored-By` line.

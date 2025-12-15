# Claude Code TTS Hooks

Audio feedback for Claude Code using text-to-speech. Hear summaries of Claude's responses and permission requests.

## Features

- **Response summaries** - After Claude responds, hear a TTS summary of what it did
- **Permission announcements** - Hear what permission Claude is requesting before you approve
- **Interrupt support** - New audio cancels currently playing audio with a transition chime
- **SSH support** - Works on remote servers via reverse tunnel

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [Groq API key](https://console.groq.com/) (free tier works fine)

## Installation

### 1. Set Up Python Environment

```bash
uv python install 3.13
uv venv --python 3.13
uv pip install kokoro soundfile pip
```

### 2. Start the TTS Server

```bash
uv run kokoro-server.py --port 20202
```

### 3. Install the Hooks

```bash
mkdir -p ~/.claude/hooks

cp * ~/.claude/hooks/

# or

cp claude-code-kokoro-tts-summary.sh ~/.claude/hooks/
cp claude-code-kokoro-tts-permission.sh ~/.claude/hooks/
```

### 4. Configure Claude Code

Add to `~/.claude/settings.local.json`:

```json
{
  "env": {
    "SUMMARY_GROQ_API_KEY": "your-groq-api-key"
  },
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/claude-code-kokoro-tts-summary.sh",
            "timeout": 10
          }
        ]
      }
    ],
    "PermissionRequest": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/claude-code-kokoro-tts-permission.sh",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

## Hooks

| Hook | Event | Description |
|------|-------|-------------|
| `claude-code-kokoro-tts-summary.sh` | Stop | Summarizes Claude's response after completion |
| `claude-code-kokoro-tts-permission.sh` | PermissionRequest | Announces what permission Claude is requesting |

## Remote Usage (SSH)

Run the TTS server on your local machine, then SSH with a reverse tunnel:

```bash
# Local machine
uv run kokoro-server.py --port 20202

# SSH to remote
ssh -R 20202:localhost:20202 user@remote-server
```

The hooks on the remote server will send audio back through the tunnel to your local TTS server.

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SUMMARY_GROQ_API_KEY` | Yes | - | Groq API key |
| `SUMMARY_GROQ_MODEL_LARGE` | No | `openai/gpt-oss-120b` | Model for long response summaries |
| `SUMMARY_GROQ_MODEL_SMALL` | No | `llama-3.1-8b-instant` | Model for short responses and permissions |
| `SUMMARY_AUDIO_PORT` | No | `20202` | TTS server port |

## How It Works

**Response Summaries (Stop hook):**
- Short responses (<300 chars): Cleaned for TTS (removes markdown)
- Long responses: Summarized to 1-3 sentences in first person
- Content-aware: Actions use past tense, explanations get summarized, questions kept as-is

**Permission Announcements (PermissionRequest hook):**
- Extracts tool name and parameters
- Generates brief announcement like "Permission requested: Bash command to check disk space"
- Non-blocking to avoid delaying the permission dialog

**TTS Server:**
- Async architecture handles multiple connections
- New requests interrupt currently playing audio
- Plays a two-note chime (G5→C6) on interruption
- Rapid requests: plays ~0.8s snippets of each, then full final message

## Voices

The default voice (`af_heart`) sounds great. The other Kokoro voices aren't as good, but you can find them here: [VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)

## Debugging

Logs are written to:
- `~/.claude/hooks/claude-code-kokoro-tts-summary.output`
- `~/.claude/hooks/claude-code-kokoro-tts-permission.output`

## Groq Rate Limits

Free tier (as of December 2024):
- ~1,000 long summaries/day (`openai/gpt-oss-120b`)
- ~14,400 short summaries/day (`llama-3.1-8b-instant`)

## License

MIT

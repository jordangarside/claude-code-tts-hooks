# Claude Code TTS Server

Audio feedback for Claude Code using text-to-speech. Hear summaries of Claude's responses and permission requests.

## Features

- **Response summaries** - After Claude responds, hear a TTS summary of what it did
- **Permission announcements** - Hear what permission Claude is requesting before you approve
- **Interrupt support** - New audio cancels currently playing audio with a transition chime
- **SSH support** - Works on remote servers via reverse tunnel
- **REST API** - Control the TTS server programmatically

## Audio Examples

**Response summary** - After Claude completes a task

https://github.com/user-attachments/assets/217b3273-092b-4a70-9b6a-1ed45777b60a

**Permission request** - Before a tool requires approval

https://github.com/user-attachments/assets/df42f17a-2c52-4346-91df-0510c49a8655

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [Groq API key](https://console.groq.com/) (free tier works fine) **OR** [Ollama](https://ollama.ai/) for local inference

## Installation

### 1. Install Dependencies

```bash
➜ uv sync
```

### 2. Configure the Server

Copy the example environment file and add your API key:

```bash
➜ cp .env.example .env
```

Edit `.env` and set your Groq API key:

```bash
SUMMARY_GROQ_API_KEY=your-groq-api-key-here
```

See [Configuration](#configuration) below for all available options.

### 3. Start the TTS Server

```bash
➜ uv run tts-server
```

### 4. Install the Hooks

```bash
➜ mkdir -p ~/.claude/hooks
➜ cp claude-code-hooks/* ~/.claude/hooks/
```

### 5. Configure Claude Code Hooks

Add to `~/.claude/settings.local.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/summary-tts.sh",
            "timeout": 3
          }
        ]
      }
    ],
    "PermissionRequest": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/permission-tts.sh",
            "timeout": 3
          }
        ]
      }
    ]
  }
}
```

### 6. Test it Out

```bash
➜ claude --model=haiku -p 'tell me a fantasy story in 1 paragraph'
```

You may need to load `claude` and check the hooks are loaded with the `/hooks` command.

## Architecture

```
claude_code_tts_server/         # Python package
├── main.py                     # FastAPI server entry point
├── config.py                   # Configuration with Pydantic
├── api/                        # REST API endpoints
├── core/                       # Audio manager, playback, sounds
├── summarizers/                # LLM backends (Groq)
└── tts/                        # TTS backends (Kokoro)

claude-code-hooks/              # Shell script wrappers
├── summary-tts.sh              # Stop hook -> POST /summarize
└── permission-tts.sh           # PermissionRequest hook -> POST /permission
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/summarize` | Full pipeline: transcript -> summary -> TTS |
| POST | `/permission` | Permission announcement pipeline |
| POST | `/speak` | Direct TTS (skip summarization) |
| GET | `/queue` | Queue status |
| POST | `/queue/clear` | Clear all pending audio |
| POST | `/queue/skip` | Skip currently playing audio |

## Remote Usage (SSH)

Run the TTS server on your local machine, then SSH with a reverse tunnel:

```bash
# Local machine
➜ uv run tts-server

# SSH to remote (forward port 20202 back to local)
➜ ssh -R 20202:localhost:20202 user@remote-server
```

Make sure you have the hooks copied over to the remote server, as well as updated the `~/.claude/settings.json` on the server to use them.

The hooks on the remote server will send requests through the tunnel to your local TTS server for audio playback.

## Configuration

All settings can be configured via environment variables in `.env` or CLI args. CLI args take precedence over env vars.

### Summarizer Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SUMMARY_BACKEND` | `groq` | Backend: `groq` or `ollama` |
| `SUMMARY_GROQ_API_KEY` | - | Groq API key (required for groq) |
| `SUMMARY_GROQ_MODEL_LARGE` | `openai/gpt-oss-120b` | Groq model for long responses |
| `SUMMARY_GROQ_MODEL_SMALL` | `llama-3.1-8b-instant` | Groq model for short responses |
| `SUMMARY_OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `SUMMARY_OLLAMA_MODEL_LARGE` | `qwen3:4b-instruct-2507-q4_K_M` | Ollama model for long responses |
| `SUMMARY_OLLAMA_MODEL_SMALL` | `qwen3:4b-instruct-2507-q4_K_M` | Ollama model for short responses |

### Server Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `TTS_SERVER_HOST` | `127.0.0.1` | Host to bind to |
| `SUMMARY_AUDIO_PORT` | `20202` | Port to listen on |
| `TTS_SERVER_LOG_LEVEL` | `INFO` | Log level |

### Audio Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIO_INTERRUPT` | `true` | Allow new audio to interrupt |
| `AUDIO_MIN_DURATION` | `1.5` | Seconds before interrupt allowed |
| `AUDIO_QUEUE` | `true` | Queue messages to play in order |
| `AUDIO_MAX_QUEUE` | `10` | Maximum queue depth |
| `AUDIO_INTERRUPT_CHIME` | `true` | Play chime on interrupt |
| `AUDIO_DROP_SOUND` | `true` | Play sound when messages dropped |
| `AUDIO_SPEED` | `1.0` | Playback speed multiplier (requires rubberband, see below) |

### TTS Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `TTS_BACKEND` | `kokoro` | Backend: `kokoro` |
| `TTS_KOKORO_VOICE` | `af_heart` | Kokoro voice |
| `TTS_KOKORO_LANG` | `a` | Kokoro language code |

### Using Ollama (Local LLM)

For local inference without API keys, install [Ollama](https://ollama.ai/) and configure via `.env`:

```bash
SUMMARY_BACKEND=ollama
SUMMARY_OLLAMA_MODEL_LARGE=llama3.1:8b
SUMMARY_OLLAMA_MODEL_SMALL=llama3.2:1b
```

Or use CLI args:

```bash
➜ uv run tts-server --summarizer ollama
```

**Note:** Local inference is slower than Groq, especially without a GPU. Expect 2-10+ seconds per summary depending on your hardware.

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
- FastAPI-based REST API
- Async architecture handles multiple connections
- Configurable interrupt and queue behavior
- Optional audio indicators (chime on interrupt, blip on skip)

### Server Options

```bash
➜ uv run tts-server [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--port` | `20202` | Port to listen on |
| `--host` | `127.0.0.1` | Host to bind to |
| `--voice` | `af_heart` | Kokoro voice to use |
| `--lang` | `a` | Language code (`a` = American English) |
| `--interrupt` | `true` | Allow new audio to interrupt playing audio |
| `--no-interrupt` | - | Disable interrupts (play to completion) |
| `--min-duration` | `1.5` | Seconds to play before allowing interrupt |
| `--queue` | `true` | Queue messages to play in order |
| `--no-queue` | - | Skip to latest message only |
| `--max-queue` | `10` | Maximum queue depth (oldest dropped) |
| `--interrupt-chime` | `true` | Play chime when interrupting audio |
| `--no-interrupt-chime` | - | Disable interrupt chime |
| `--drop-sound` | `true` | Play blip when messages are skipped |
| `--no-drop-sound` | - | Disable drop sound |
| `--speed` | `1.0` | Playback speed (1.3 = 30% faster, requires rubberband) |
| `--log-level` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--summarizer` | `groq` | Summarizer backend: `groq` or `ollama` |
| `--ollama-model-large` | `llama3.1:8b` | Ollama model for long responses |
| `--ollama-model-small` | `llama3.2:1b` | Ollama model for short responses/permissions |
| `--ollama-url` | `http://localhost:11434` | Ollama server URL |

### Interrupt and Queue Behavior

The `--interrupt` and `--queue` options are independent and combine as follows:

| interrupt | queue | Behavior |
|-----------|-------|----------|
| true | true | **Default.** Interrupt after min-duration to play next queued message. All messages eventually play. |
| true | false | Interrupt after min-duration to skip to latest. Intermediate messages are dropped. |
| false | true | Play all messages to completion in order. No interrupts. |
| false | false | Play current to completion, then skip to latest. Intermediate messages are dropped. |

### Audio Indicators

| Sound | When | Option |
|-------|------|--------|
| **Interrupt Chime** (two-note G5 -> C6) | Playing audio is interrupted | `--interrupt-chime` / `--no-interrupt-chime` |
| **Drop tone** (short blip) | Message skipped without playing | `--drop-sound` / `--no-drop-sound` |

### Playback Speed

Speed up audio playback while preserving pitch using the `--speed` option:

```bash
➜ uv run tts-server --speed 1.3  # 30% faster
```

**Requires rubberband** (optional dependency):

```bash
# macOS
➜ brew install rubberband

# Linux (Debian/Ubuntu)
➜ sudo apt install rubberband-cli

# Then install Python bindings
➜ uv sync --extra speed
```

The rubberband library provides high-quality pitch-preserving time-stretching. If `AUDIO_SPEED` is left at the default (1.0), rubberband is not required.

## Voices

The default voice (`af_heart`) sounds great. The other Kokoro voices aren't as good, but you can find them here: [VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)

## Debugging

Logs are written to:
- `~/.claude/hooks/summary-tts.output`
- `~/.claude/hooks/permission-tts.output`

## Groq Rate Limits

Free tier (as of December 2025):
- ~1,000 long summaries/day (`openai/gpt-oss-120b`)
- ~14,400 short summaries/day (`llama-3.1-8b-instant`)

## License

MIT

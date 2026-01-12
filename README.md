# clodxy

A simple custom proxy for Claude Code -> Any (OpenAI-compatible) Backend.

Supports the bare minimum necessary (messages, streaming, tools, vision).

## Security Warning

**NEVER expose this proxy to the public internet.** This proxy is designed for **local-only use** and has no authentication or authorization mechanisms. The moment you bind this to a public-facing interface, you're inviting anyone on the internet to use your API keys and backend credentials. That's a skill issue waiting to happen. Keep it local.

## Requirements

- Python 3.12+
- [Claude Code CLI](https://github.com/anthropics/claude-code) must be installed
- [uv](https://github.com/astral-sh/uv) (for running from source)

## Installation

No installation needed - run directly via uvx:

```bash
uvx clodxy
```

## Configuration

Create a config file at `~/.config/clodxy/config.json` (or set `CLODXY_CONFIG` to customize the path).

See `config.example.json` for reference:

```json
{
  "backends": {
    "openai": {
      "api_base": "https://api.openai.com/v1",
      "api_key": "sk-your-api-key-here",
      "skip_last_assistant_message": false,
      "models": {
        "gpt-4o": {
          "context_size": 128000,
          "reasoning": false,
          "vision": true
        }
      }
    }
  },
  "default": {
    "backend": "openai",
    "model": "gpt-4o"
  }
}
```

### Configuration Options

#### Backend fields
- `api_base` - The base URL of the OpenAI-compatible API
- `api_key` - API key for the backend (optional, defaults to `"not-needed"`)
- `skip_last_assistant_message` - Skip the last assistant message in the Anthropic→OpenAI conversion. **Only applies when both this is true AND the model has `reasoning: true`.** Enable this for backends like llama.cpp that don't accept assistant prefills as the final message.

#### Model fields
- `context_size` - Maximum context window size in tokens
- `reasoning` - Whether this is a reasoning/thinking model (e.g., o1, o3-mini, deepseek-reasoner)
- `vision` - Whether this model supports vision/images

## Usage

Run clodxy - it will start the proxy and launch Claude Code:

```bash
clodxy
clodxy /path/to/project
```

### CLI Options

```bash
clodxy --help              # Show help
clodxy --version           # Show version
clodxy --list              # List available backends and models
clodxy --validate-config   # Validate configuration
clodxy --port 9000         # Custom port (default: auto-assign)
clodxy --host 0.0.0.0      # Custom host
```

### Passthrough

Use `--` to separate clodxy options from claude options:

```bash
clodxy --port 9000 -- --prompt "help me debug"
```

### Logs

Logs are written to `~/.cache/clodxy/`:
- `clodxy.log` - Application logs
- `uvicorn.log` - Server logs

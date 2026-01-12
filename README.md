# clodxy

A simple custom proxy for Claude Code -> Any (OpenAI-compatible) Backend.

Supports the bare minimum necessary (messages, streaming, tools, vision).

## Requirements

- Python 3.12+
- [Claude Code CLI](https://github.com/anthropics/claude-code) must be installed
- [uv](https://github.com/astral-sh/uv) (for running from source)

## Installation

Clone and run with uv:

```bash
git clone https://github.com/lyssieth/clodxy.git
cd clodxy
./clodxy.sh
```

Or install via pip/uv:

```bash
pip install clodxy
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

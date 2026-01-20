#!/usr/bin/env python3
"""Start clodxy proxy and launch Claude Code with proper env vars."""

import argparse
import httpx
import json
import os
import re
import readline
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from clodxy.config import load_config, DEFAULT_CONFIG_PATH, Backend, Model, ChosenDefault, Config

# Default proxy settings
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "0"  # 0 = let OS pick available port

# Log file locations
LOG_DIR = Path.home() / ".cache" / "clodxy"
UVICORN_LOG_PATH = LOG_DIR / "uvicorn.log"
APP_LOG_PATH = LOG_DIR / "clodxy.log"


def _make_parser() -> argparse.ArgumentParser:
  """Create the argument parser.

  Returns:
    Configured ArgumentParser instance.
  """
  parser = argparse.ArgumentParser(
    prog="clodxy",
    description="Start the clodxy proxy and launch Claude Code",
    add_help=False,  # Always false, we handle --help manually
  )
  parser.add_argument(
    "--help",
    "-h",
    action="store_true",
    help="Show this help message and exit",
  )
  parser.add_argument(
    "--version",
    action="store_true",
    help="Show version and exit",
  )
  parser.add_argument(
    "--list",
    action="store_true",
    help="List available backends and models",
  )
  parser.add_argument(
    "--backend",
    help="Backend to use (overrides config default)",
  )
  parser.add_argument(
    "--model",
    help="Model to use (overrides config default)",
  )
  parser.add_argument(
    "--validate-config",
    action="store_true",
    help="Validate configuration file and exit",
  )
  parser.add_argument(
    "--host",
    default=DEFAULT_HOST,
    help=f"Proxy host (default: {DEFAULT_HOST})",
  )
  parser.add_argument(
    "--port",
    default=DEFAULT_PORT,
    help=f"Proxy port (default: {DEFAULT_PORT}, 0 = auto-assign)",
  )
  parser.add_argument(
    "--init",
    action="store_true",
    help="Initialize configuration file interactively",
  )
  parser.add_argument(
    "claude_args",
    nargs="*",
    help="Arguments to pass to claude (use '--' to separate clodxy args)",
  )
  return parser


def _prompt(prompt: str, default: str | None = None, required: bool = True) -> str | None:
  """Prompt user for input with optional default.

  Args:
    prompt: The prompt text to display
    default: Default value if user presses Enter (None means no default)
    required: If True, requires non-empty input unless default is provided

  Returns:
    The user input, or None if empty input allowed and user pressed Enter
  """
  if default is not None:
    result = input(f"{prompt} [{default}]: ").strip()
    return result if result else default

  while True:
    result = input(f"{prompt}: ").strip()
    if result or not required:
      return result or None


def _prompt_bool(prompt: str, default: bool = False) -> bool:
  """Prompt user for yes/no with optional default."""
  default_str = "y" if default else "n"
  while True:
    result = input(f"{prompt} (y/n) [{default_str}]: ").strip().lower()
    if not result:
      return default
    if result in ("y", "yes"):
      return True
    if result in ("n", "no"):
      return False


def _init_config() -> None:
  """Initialize configuration file interactively."""
  print("\n| Initializing clodxy configuration\n")

  # Configure readline for better input handling
  readline.parse_and_bind("tab: complete")

  # Check if config exists
  if DEFAULT_CONFIG_PATH.exists():
    overwrite = _prompt_bool(
      f"Config already exists at {DEFAULT_CONFIG_PATH}. Overwrite?", default=False
    )
    if not overwrite:
      print("\n| Aborted")
      sys.exit(0)

  # Gather configuration
  backend_name = _prompt("Backend name (e.g., openai, deepseek)")
  api_base = _prompt("API base URL", "https://api.openai.com/v1")
  api_key = _prompt("API key (optional, press Enter to skip)", "")
  model_name = _prompt("Model name (e.g., gpt-4o)")
  context_size = _prompt("Context size", "128000")

  # Type assertions - these will never be None due to prompts
  assert backend_name is not None
  assert api_base is not None
  assert api_key is not None
  assert model_name is not None
  assert context_size is not None

  is_reasoning = _prompt_bool("Is this a reasoning model?", default=False)
  supports_vision = _prompt_bool("Supports vision?", default=False)

  # For reasoning models, ask about llama-server
  skip_last_assistant = False
  if is_reasoning:
    is_llama_server = _prompt_bool("Are you running llama-server or similar?", default=False)
    if is_llama_server:
      skip_last_assistant = True

  # Build config
  config = Config(
    backends={
      backend_name: Backend(
        api_base=api_base,
        api_key=api_key if api_key else None,
        skip_last_assistant_message=skip_last_assistant,
        models={
          model_name: Model(
            context_size=int(context_size),
            reasoning=is_reasoning,
            vision=supports_vision,
          )
        },
      )
    },
    default=ChosenDefault(backend=backend_name, model=model_name),
  )

  # Create config directory
  config_dir = DEFAULT_CONFIG_PATH.parent
  config_dir.mkdir(parents=True, exist_ok=True)

  # Write config
  with open(DEFAULT_CONFIG_PATH, "w") as f:
    json.dump(
      {
        "backends": {
          backend_name: {
            "api_base": config.backends[backend_name].api_base,
            **(
              {"api_key": config.backends[backend_name].api_key}
              if config.backends[backend_name].api_key
              else {}
            ),
            "skip_last_assistant_message": config.backends[
              backend_name
            ].skip_last_assistant_message,
            "models": {
              model_name: {
                "context_size": config.backends[backend_name].models[model_name].context_size,
                "reasoning": config.backends[backend_name].models[model_name].reasoning,
                "vision": config.backends[backend_name].models[model_name].vision,
              }
            },
          }
        },
        "default": {"backend": config.default.backend, "model": config.default.model},
      },
      f,
      indent=2,
    )
    f.write("\n")

  print(f"\n| Configuration written to {DEFAULT_CONFIG_PATH}")

  # Validate
  try:
    load_config()
    print("| Configuration is valid!")
  except (FileNotFoundError, ValueError) as e:
    print(f"! Validation warning: {e}")

  print("\n| You can now run: clodxy")
  sys.exit(0)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
  """Parse command-line arguments.

  Arguments before '--' are parsed by clodxy.
  Arguments after '--' are passed directly to claude.
  """
  parser = _make_parser()

  if argv is None:
    argv = sys.argv[1:]

  try:
    delimiter_idx = argv.index("--")
  except ValueError:
    # No '--' found, parse all args
    return parser.parse_args(argv)

  # Parse only args before '--'
  clodxy_args = argv[:delimiter_idx]
  claude_passthrough = argv[delimiter_idx + 1 :]

  args = parser.parse_args(clodxy_args)
  args.claude_args.extend(claude_passthrough)
  return args


def main():
  args = parse_args()

  if args.help:
    _make_parser().print_help()
    print("\nPassthrough:")
    print("  Use '--' to separate clodxy options from claude options.")
    print("  Example: clodxy --port 9000 -- --prompt 'write code'")
    print("\nLogs:")
    print(f"  App:      {APP_LOG_PATH}")
    print(f"  Uvicorn:  {UVICORN_LOG_PATH}")
    sys.exit(0)

  if args.version:
    try:
      from clodxy import __version__

      print(f"clodxy {__version__}")
    except (ImportError, AttributeError):
      print("clodxy (version unknown)")
    sys.exit(0)

  if args.list:
    config = load_config()
    print("Available backends and models:\n")
    for backend_name, backend in config.backends.items():
      is_default = backend_name == config.default.backend
      default_marker = " (default)" if is_default else ""
      print(f"  {backend_name}{default_marker}")
      for model_name in backend.models.keys():
        is_default_model = is_default and model_name == config.default.model
        model_marker = " <-" if is_default_model else ""
        print(f"    - {model_name}{model_marker}")
    sys.exit(0)

  if args.validate_config:
    try:
      config = load_config()
      print("Config is valid!")
      print(f"  Backend: {config.default.backend}")
      print(f"  Model: {config.default.model}")
      print(f"  API base: {config.backends[config.default.backend].api_base}")
      sys.exit(0)
    except (FileNotFoundError, ValueError) as e:
      print(f"Config error: {e}")
      sys.exit(1)

  if args.init:
    _init_config()

  # Check if claude CLI is available
  if not shutil.which("claude"):
    print("! Error: 'claude' CLI not found in PATH")
    print("  Please install Claude Code CLI first:")
    print("  https://github.com/anthropics/claude-code")
    sys.exit(1)

  # Load and validate config, apply CLI overrides
  config = load_config()
  backend_name = config.default.backend
  model_name = config.default.model

  if args.backend:
    if args.backend not in config.backends:
      available = ", ".join(config.backends.keys())
      print(f"! Error: Backend '{args.backend}' not found.")
      print(f"  Available: {available}")
      sys.exit(1)
    backend_name = args.backend

  if args.model:
    backend = config.backends[backend_name]
    if args.model not in backend.models:
      available = ", ".join(backend.models.keys())
      print(f"! Error: Model '{args.model}' not found in backend '{backend_name}'.")
      print(f"  Available: {available}")
      sys.exit(1)
    model_name = args.model

  # Set up environment for both uvicorn and claude
  proxy_env = os.environ.copy()
  proxy_env["CLODXY_BACKEND"] = backend_name
  proxy_env["CLODXY_MODEL"] = model_name

  # Start clodxy proxy in background
  print("| Starting clodxy proxy...")
  LOG_DIR.mkdir(parents=True, exist_ok=True)
  uvicorn_log = open(UVICORN_LOG_PATH, "w")
  proxy = subprocess.Popen(
    ["uvicorn", "clodxy.main:app", "--host", args.host, "--port", args.port],
    stdout=uvicorn_log,
    stderr=uvicorn_log,
    env=proxy_env,
  )

  # Poll health endpoint until ready (max 10 seconds)
  base_url = None
  for attempt in range(50):  # 50 * 0.2s = 10s max
    time.sleep(0.2)
    if proxy.poll() is not None:
      print("! Failed to start proxy")
      print(f"  Check logs at: {UVICORN_LOG_PATH}")
      sys.exit(1)

    # Try to get the actual port from uvicorn output if using auto-port
    if args.port == "0" and base_url is None:
      uvicorn_log.flush()
      with open(UVICORN_LOG_PATH, "r") as f:
        for line in f:
          if "Uvicorn running on" in line or "Application startup complete" in line:
            # Parse port from log line like "Uvicorn running on http://127.0.0.1:12345"
            match = re.search(r"http://[\d.]+:(\d+)", line)
            if match:
              actual_port = match.group(1)
              base_url = f"http://{args.host}:{actual_port}"
              break

    # If we have a base_url, try health check
    if base_url:
      try:
        response = httpx.get(f"{base_url}/health", timeout=0.5)
        if response.status_code == 200:
          break
      except Exception:
        pass  # Not ready yet

  # Final check if proxy is running
  if proxy.poll() is not None:
    print("! Failed to start proxy")
    print(f"  Check logs at: {UVICORN_LOG_PATH}")
    sys.exit(1)

  # If auto-port and we didn't find it in logs, try to discover it
  if args.port == "0" and base_url is None:
    print("! Could not determine proxy port")
    print(f"  Check logs at: {UVICORN_LOG_PATH}")
    sys.exit(1)

  # Use explicit port if not auto-port
  if args.port != "0":
    base_url = f"http://{args.host}:{args.port}"

  # base_url should always be set at this point
  assert base_url is not None

  print(f"| Proxy running on {base_url}")
  print(f"| Using {model_name} from {backend_name}")

  # Set env vars for Claude Code
  env = os.environ.copy()
  env["ANTHROPIC_AUTH_TOKEN"] = "clodxy-local-key"
  env["ANTHROPIC_BASE_URL"] = base_url
  env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model_name
  env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model_name
  env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model_name

  print("| Environment configured")
  if args.claude_args:
    print(f"| Launching claude {' '.join(args.claude_args)}\n")
  else:
    print("| Launching claude\n")

  try:
    # Run claude with any passed args
    claude = subprocess.run(
      ["claude", *args.claude_args],
      env=env,
    )
    exit_code = claude.returncode
  except KeyboardInterrupt:
    print("\n\n| Interrupted")
    exit_code = 130
  finally:
    # Clean up proxy
    print("\n| Shutting down proxy...")
    proxy.send_signal(signal.SIGTERM)
    try:
      proxy.wait(timeout=2)
    except subprocess.TimeoutExpired:
      proxy.kill()
    uvicorn_log.close()
    print("| Cleanup complete")

  sys.exit(exit_code)


if __name__ == "__main__":
  main()

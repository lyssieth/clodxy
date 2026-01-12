#!/usr/bin/env python3
"""Start clodxy proxy and launch Claude Code with proper env vars."""

import argparse
import httpx
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from clodxy.config import load_config

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
    "claude_args",
    nargs="*",
    help="Arguments to pass to claude (use '--' to separate clodxy args)",
  )
  return parser


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

  # Check if claude CLI is available
  if not shutil.which("claude"):
    print("! Error: 'claude' CLI not found in PATH")
    print("  Please install Claude Code CLI first:")
    print("  https://github.com/anthropics/claude-code")
    sys.exit(1)

  # Start clodxy proxy in background
  print("| Starting clodxy proxy...")
  LOG_DIR.mkdir(parents=True, exist_ok=True)
  uvicorn_log = open(UVICORN_LOG_PATH, "w")
  proxy = subprocess.Popen(
    ["uvicorn", "clodxy.main:app", "--host", args.host, "--port", args.port],
    stdout=uvicorn_log,
    stderr=uvicorn_log,
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

  config = load_config()
  print(f"| Proxy running on {base_url}")
  print(f"| Using {config.default.model} from {config.default.backend}")

  # Set env vars for Claude Code
  env = os.environ.copy()
  env["ANTHROPIC_AUTH_TOKEN"] = "clodxy-local-key"
  env["ANTHROPIC_BASE_URL"] = base_url
  env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = config.default.model
  env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = config.default.model
  env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = config.default.model

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

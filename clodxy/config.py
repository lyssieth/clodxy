from dataclasses import dataclass
import json
from pathlib import Path
from os import getenv

from dacite import from_dict, DaciteError


@dataclass
class Model:
  context_size: int
  reasoning: bool = False
  vision: bool = False


@dataclass
class Backend:
  api_base: str
  models: dict[str, Model]
  api_key: str | None = None
  # Skip the last assistant message in Anth -> OpenAI conversion.
  # Only applies when BOTH this is true AND the model has reasoning=true.
  # Required for llama.cpp and other servers with thinking/reasoning models
  # that don't accept assistant prefills as the final message.
  skip_last_assistant_message: bool = False


@dataclass
class ChosenDefault:
  backend: str
  model: str


@dataclass
class Config:
  backends: dict[str, Backend]
  default: ChosenDefault


HOME_ENV = getenv("HOME")
if HOME_ENV is None:
  raise RuntimeError("No $HOME envvar found.")
HOME = Path(HOME_ENV)
DEFAULT_CONFIG_PATH = HOME / ".config" / "clodxy" / "config.json"
EXAMPLE_CONFIG_URL = "https://github.com/lyssieth/clodxy/blob/main/config.example.json"


def _get_config_path() -> Path:
  """Get config path from environment or default location."""
  if env_path := getenv("CLODXY_CONFIG"):
    return Path(env_path)
  return DEFAULT_CONFIG_PATH


def load_config():
  path = _get_config_path()
  if not path.exists():
    raise FileNotFoundError(
      f"Config not found at {path}\n"
      f"  1. Copy config.example.json to {path}\n"
      f"  2. Or set CLODXY_CONFIG environment variable to your config file"
    )

  try:
    with open(path, "r") as f:
      result = from_dict(data_class=Config, data=json.load(f))
  except json.JSONDecodeError as e:
    raise ValueError(f"Invalid JSON in config file: {e}") from e
  except DaciteError as e:
    raise ValueError(f"Config validation error: {e}") from e

  # Validate that default backend and model exist
  if result.default.backend not in result.backends:
    available = ", ".join(result.backends.keys())
    raise ValueError(
      f"Default backend '{result.default.backend}' not found. Available backends: {available}"
    )

  backend = result.backends[result.default.backend]
  if result.default.model not in backend.models:
    available = ", ".join(backend.models.keys())
    raise ValueError(
      f"Default model '{result.default.model}' not found in backend '{result.default.backend}'. "
      f"Available models: {available}"
    )

  return result


def get_selected_model_id() -> str:
  """Get the model ID for the currently selected default model."""
  config = load_config()
  return config.default.model

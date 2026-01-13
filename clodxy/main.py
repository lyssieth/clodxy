import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import StreamingResponse
import httpx

from .config import load_config
from .translate import (
  anthropic_dict_to_openai_request,
  openai_dict_to_anthropic_response,
  TranslationOptions,
  VisionNotSupportedError,
)


# Request timeout (5 minutes for long responses)
REQUEST_TIMEOUT = 300.0


_log_path = Path.home() / ".cache" / "clodxy" / "clodxy.log"
_log_path.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
  filename=_log_path,
  filemode="w",
  level=logging.INFO,
  format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

config = load_config()

# CLI overrides via environment variables
cli_backend = os.environ.get("CLODXY_BACKEND")
cli_model = os.environ.get("CLODXY_MODEL")

backends = config.backends
chosen = config.default

# Apply CLI overrides if present
if cli_backend:
  if cli_backend not in backends:
    raise KeyError(
      f"Backend '{cli_backend}' not found in config. Available: {list(backends.keys())}"
    )
  chosen.backend = cli_backend

backend = backends[chosen.backend]

if cli_model:
  if cli_model not in backend.models:
    raise KeyError(
      f"Model '{cli_model}' not found in backend '{chosen.backend}'. Available: {list(backend.models.keys())}"
    )
  chosen.model = cli_model

api_base = backend.api_base
api_key = backend.api_key or "not-needed"

model = backend.models[chosen.model]

# Create translation options from backend configuration
translation_options = TranslationOptions(
  skip_last_assistant_message=backend.skip_last_assistant_message and model.reasoning,
  supports_vision=model.vision,
)

# Singleton HTTP client for backend connections
_client = httpx.AsyncClient(
  headers={
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
  },
  timeout=REQUEST_TIMEOUT,
)

app = FastAPI()


@app.middleware("http")
async def log_unimplemented_routes(
  request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
  """Log requests to unimplemented routes."""
  response = await call_next(request)

  # Log 404s as potential unimplemented routes
  if response.status_code == 404:
    logger.warning(f"Unimplemented route: {request.method} {request.url.path}")

  return response


@app.post("/v1/messages/count_tokens")
async def count_tokens_stub():
  """Token counting stub - Claude Code doesn't need accurate counts."""
  return {"input_tokens": 1}


@app.get("/health")
async def health_check():
  """Health check endpoint - returns backend and model info."""
  return {
    "status": "ok",
    "backend": chosen.backend,
    "model": chosen.model,
    "api_base": api_base,
  }


@app.post("/api/event_logging/batch")
async def telemetry_stub():
  """Stub for Claude Code telemetry - we don't care about analytics."""
  return {"status": "ok"}


@app.post("/v1/messages")
async def proxy_messages(request: Request) -> Response:
  """Proxy Anthropic API requests to OpenAI-compatible backend."""
  # Get Anthropic format request
  anthropic_req = await request.json()

  try:
    # Translate to OpenAI with backend options
    openai_req = anthropic_dict_to_openai_request(anthropic_req, translation_options)
  except VisionNotSupportedError as e:
    logger.error(f"Vision not supported error: {e}")
    raise HTTPException(
      status_code=400,
      detail=str(e),
    ) from e

  # Check if streaming
  is_streaming = openai_req.get("stream", False)

  if is_streaming:
    return StreamingResponse(
      stream_openai_to_anthropic(openai_req),
      media_type="text/event-stream",
    )
  else:
    # Non-streaming: simple request/response
    try:
      response = await _client.post(
        f"{api_base}/chat/completions",
        json=openai_req,
      )
      response.raise_for_status()
      openai_resp = response.json()
      anthropic_resp = openai_dict_to_anthropic_response(openai_resp)
      return Response(anthropic_resp)
    except httpx.HTTPStatusError as e:
      logger.error(f"HTTP error from backend: {e.response.status_code} - {e.response.text}")
      raise HTTPException(
        status_code=e.response.status_code,
        detail=f"Backend error: {e.response.text}",
      ) from e
    except httpx.ConnectError as e:
      logger.error(f"Failed to connect to backend at {api_base}: {e}")
      raise HTTPException(
        status_code=503,
        detail=f"Could not connect to backend at {api_base}. Check your network connection.",
      ) from e
    except httpx.TimeoutException as e:
      logger.error(f"Request to backend timed out: {e}")
      raise HTTPException(
        status_code=504,
        detail="Request to backend timed out. Try again later.",
      ) from e


async def stream_openai_to_anthropic(openai_req: dict[str, Any]):
  """
  Convert OpenAI streaming format to Anthropic streaming format.

  OpenAI: data: {"choices":[{"delta":{"content":"..."}}]}\n\n
  Anthropic: event: content_block_delta\ndata: {...}\n\n
  """

  async with _client.stream(
    "POST",
    f"{api_base}/chat/completions",
    json=openai_req,
  ) as response:
    response.raise_for_status()

    # Generate message ID (UUIDv4)
    message_id = f"msg_{uuid.uuid4().hex}"

    # Send message_start event
    yield "event: message_start\n"
    yield f"data: {json.dumps({'type': 'message_start', 'message': {'id': message_id, 'type': 'message', 'role': 'assistant', 'content': [], 'model': openai_req['model'], 'stop_reason': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}})}\n\n"

    content_block_index = 0
    content_block_started = False
    tool_calls_buffer: dict[int, dict[str, Any]] = {}

    async for line in response.aiter_lines():
      if not line.strip() or not line.startswith("data: "):
        continue

      data_str = line[6:].strip()
      if data_str == "[DONE]":
        break

      try:
        chunk = json.loads(data_str)
      except json.JSONDecodeError:
        logger.debug(f"Failed to parse JSON chunk: {data_str[:100]}")
        continue

      choices = chunk.get("choices", [])
      if not choices:
        continue

      delta = choices[0].get("delta", {})
      finish_reason = choices[0].get("finish_reason")

      # Handle text content
      if "content" in delta and delta["content"]:
        if not content_block_started:
          yield "event: content_block_start\n"
          yield f"data: {json.dumps({'type': 'content_block_start', 'index': content_block_index, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
          content_block_started = True

        yield "event: content_block_delta\n"
        yield f"data: {json.dumps({'type': 'content_block_delta', 'index': content_block_index, 'delta': {'type': 'text_delta', 'text': delta['content']}})}\n\n"

      # Handle tool calls (buffered because they come in chunks)
      if "tool_calls" in delta:
        for tool_call in delta["tool_calls"]:
          idx = tool_call["index"]
          if idx not in tool_calls_buffer:
            tool_calls_buffer[idx] = {
              "id": tool_call.get("id", ""),
              "name": tool_call.get("function", {}).get("name", ""),
              "arguments": "",
            }

          if "function" in tool_call and "arguments" in tool_call["function"]:
            tool_calls_buffer[idx]["arguments"] += tool_call["function"]["arguments"]

      # Handle finish
      if finish_reason:
        # Close any open content blocks
        if content_block_started:
          yield "event: content_block_stop\n"
          yield f"data: {json.dumps({'type': 'content_block_stop', 'index': content_block_index})}\n\n"
          content_block_index += 1

        # Emit completed tool calls
        for _, tool_call in sorted(tool_calls_buffer.items()):
          tool_use_id = tool_call["id"] or f"toolu_{uuid.uuid4().hex}"
          yield "event: content_block_start\n"
          yield f"data: {json.dumps({'type': 'content_block_start', 'index': content_block_index, 'content_block': {'type': 'tool_use', 'id': tool_use_id, 'name': tool_call['name'], 'input': {}}})}\n\n"

          # Parse arguments JSON
          try:
            input_dict = json.loads(tool_call["arguments"])
          except json.JSONDecodeError:
            input_dict = {}

          yield "event: content_block_delta\n"
          yield f"data: {json.dumps({'type': 'content_block_delta', 'index': content_block_index, 'delta': {'type': 'input_json_delta', 'partial_json': json.dumps(input_dict)}})}\n\n"

          yield "event: content_block_stop\n"
          yield f"data: {json.dumps({'type': 'content_block_stop', 'index': content_block_index})}\n\n"
          content_block_index += 1

        # Send message_delta with stop reason
        stop_reason_map = {
          "stop": "end_turn",
          "tool_calls": "tool_use",
          "length": "max_tokens",
        }
        stop_reason = stop_reason_map.get(finish_reason, "end_turn")

        yield "event: message_delta\n"
        yield f"data: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': {'output_tokens': 0}})}\n\n"

        yield "event: message_stop\n"
        yield f"data: {json.dumps({'type': 'message_stop'})}\n\n"

import time
import json
from typing import Any

# OpenAI -> Anthropic finish reason mapping
OPENAI_TO_ANTHROPIC_STOP_REASON = {
  "stop": "end_turn",
  "length": "max_tokens",
  "tool_calls": "tool_use",
  "content_filter": "end_turn",
}


def anthropic_dict_to_openai_request(argument: dict[Any, Any]) -> dict[Any, Any]:
  """Convert Anthropic Messages API request to OpenAI Chat Completions format."""
  messages = []

  # Handle system prompt - Anthropic has separate system field, OpenAI uses messages
  system_prompt = argument.get("system")
  if system_prompt:
    messages.append({"role": "system", "content": system_prompt})

  # Convert messages
  all_msgs = argument.get("messages", [])
  for i, msg in enumerate(all_msgs):
    role = msg["role"]
    content = msg["content"]
    is_last_message = (i == len(all_msgs) - 1)

    # Skip assistant messages that would be the last message (incompatible with thinking mode)
    # This can happen when Claude Code sends tool results without a follow-up user message
    if is_last_message and role == "assistant":
      continue

    if isinstance(content, str):
      messages.append({"role": role, "content": content})
    elif isinstance(content, list):
      # Handle multimodal content (text + images + tools)
      openai_content: list[dict[str, Any]] = []
      tool_calls: list[dict[str, Any]] = []

      for block in content:
        if block["type"] == "text":
          openai_content.append({"type": "text", "text": block["text"]})
        elif block["type"] == "image":
          # Anthropic uses different source format
          source = block.get("source", {})
          media_type = source.get("type", "image/jpeg")
          data = source.get("data", "")
          openai_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{data}"},
          })
        elif block["type"] == "tool_result":
          # Convert tool result to user message with tool context
          tool_use_id = block.get("tool_use_id")
          result_content = block.get("content", "")
          if isinstance(result_content, list):
            result_text = "".join(
              b.get("text", "") if b.get("type") == "text" else str(b)
              for b in result_content
            )
          else:
            result_text = str(result_content)
          openai_content.append({
            "type": "text",
            "text": f"[Tool result for {tool_use_id}]: {result_text}",
          })
        elif block["type"] == "tool_use":
          # Convert tool_use to OpenAI's tool_calls format (for assistant messages)
          if role == "assistant":
            tool_calls.append({
              "id": block.get("id", f"call_{int(time.time() * 1000)}"),
              "type": "function",
              "function": {
                "name": block.get("name", ""),
                "arguments": json.dumps(block.get("input", {})),
              },
            })

      # Build message based on what we have
      if role == "assistant" and tool_calls:
        # Assistant message with tool calls
        assistant_msg: dict[str, Any] = {"role": "assistant", "tool_calls": tool_calls}
        if openai_content:
          assistant_msg["content"] = openai_content
        messages.append(assistant_msg)
      elif openai_content:
        messages.append({"role": role, "content": openai_content})
      elif tool_calls:
        # Only tool calls, no content (shouldn't happen but handle gracefully)
        messages.append({"role": role, "tool_calls": tool_calls, "content": ""})

  # Build OpenAI request
  openai_req: dict[str, Any] = {
    "model": argument.get("model", "gpt-4"),
    "messages": messages,
  }

  # Map common parameters
  if "max_tokens" in argument:
    openai_req["max_tokens"] = argument["max_tokens"]
  if "temperature" in argument:
    openai_req["temperature"] = argument["temperature"]
  if "top_p" in argument:
    openai_req["top_p"] = argument["top_p"]
  if "stream" in argument:
    openai_req["stream"] = argument["stream"]

  # Convert tools
  if "tools" in argument:
    openai_tools = []
    for tool in argument["tools"]:
      input_schema = tool.get("input_schema", {})
      openai_tools.append({
        "type": "function",
        "function": {
          "name": tool["name"],
          "description": tool.get("description", ""),
          "parameters": input_schema,
        },
      })
    openai_req["tools"] = openai_tools

  # Map tool_choice
  if "tool_choice" in argument:
    tool_choice = argument["tool_choice"]
    if tool_choice is None or tool_choice == "auto":
      openai_req["tool_choice"] = "auto"
    elif tool_choice == "any":
      openai_req["tool_choice"] = "required"
    elif isinstance(tool_choice, dict):
      # Specific tool choice
      openai_req["tool_choice"] = {
        "type": "function",
        "function": {"name": tool_choice["name"]},
      }

  return openai_req


def openai_dict_to_anthropic_response(argument: dict[Any, Any]) -> dict[Any, Any]:
  """Convert OpenAI Chat Completions response to Anthropic Messages API format."""
  choices = argument.get("choices", [])
  if not choices:
    return {
      "id": "msg_" + str(int(time.time() * 1000)),
      "type": "message",
      "role": "assistant",
      "content": [],
      "model": argument.get("model", ""),
      "stop_reason": "end_turn",
      "usage": {"input_tokens": 0, "output_tokens": 0},
    }

  choice = choices[0]
  message = choice.get("message", {})
  content = []
  finish_reason = choice.get("finish_reason", "stop")

  # Handle text content
  if "content" in message and message["content"]:
    content.append({"type": "text", "text": message["content"]})

  # Handle tool calls
  if "tool_calls" in message and message["tool_calls"]:
    for tool_call in message["tool_calls"]:
      function = tool_call.get("function", {})
      try:
        arguments = json.loads(function.get("arguments", "{}"))
      except json.JSONDecodeError:
        arguments = {}

      content.append({
        "type": "tool_use",
        "id": tool_call.get("id", f"toolu_{int(time.time() * 1000)}"),
        "name": function.get("name", ""),
        "input": arguments,
      })

  # Map usage
  usage = argument.get("usage", {})
  anthropic_usage = {
    "input_tokens": usage.get("prompt_tokens", 0),
    "output_tokens": usage.get("completion_tokens", 0),
  }

  return {
    "id": f"msg_{int(time.time() * 1000)}",
    "type": "message",
    "role": "assistant",
    "content": content,
    "model": argument.get("model", ""),
    "stop_reason": OPENAI_TO_ANTHROPIC_STOP_REASON.get(finish_reason, "end_turn"),
    "stop_sequence": None,
    "usage": anthropic_usage,
  }

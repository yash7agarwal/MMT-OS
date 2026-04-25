"""Google Gemini client with API surface matching utils.claude_client.

Drop-in alternative when LLM_PROVIDER=gemini is set in .env. Uses Gemini 2.0 Flash
for both default and fast modes — it's fast enough and the free tier covers our volume.

No SDK dependency: uses raw HTTP via httpx (already a project dep).
"""
from __future__ import annotations

import base64
import logging
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# gemini-flash-latest is an alias that points to whatever's currently free.
# We tried gemini-2.5-flash-lite first but its free tier is only 20 RPD which
# we burn through quickly on a single project. flash-latest has higher headroom.
DEFAULT_MODEL = "gemini-flash-latest"
FAST_MODEL = "gemini-flash-latest"

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY not set in .env. Get one free at https://aistudio.google.com/apikey"
        )
    return key


def ask(
    prompt: str,
    max_tokens: int = 1024,
    model: str = DEFAULT_MODEL,
    system: str = "",
    retries: int = 3,
) -> str:
    """Call Gemini text generation. Returns the response text."""
    # Translate Claude model names to Gemini equivalents
    if model.startswith("claude"):
        model = DEFAULT_MODEL

    payload: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    return _post(model, payload, retries)


def ask_fast(prompt: str, max_tokens: int = 512) -> str:
    return ask(prompt, max_tokens=max_tokens, model=FAST_MODEL)


def ask_vision(
    prompt: str,
    image_bytes: bytes,
    media_type: str = "image/png",
    max_tokens: int = 512,
    model: str = FAST_MODEL,
    system: str = "",
    retries: int = 3,
) -> str:
    """Call Gemini vision. Returns the response text."""
    if model.startswith("claude"):
        model = FAST_MODEL

    img_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    payload: dict = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": media_type, "data": img_b64}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    return _post(model, payload, retries)


def ask_with_tools(
    messages: list[dict],
    tools: list[dict],
    system: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    retries: int = 3,
):
    """Call Gemini with tool/function definitions.

    Accepts Anthropic-format tool schemas and messages, converts to Gemini
    format, and returns an object that mimics anthropic.types.Message so
    the caller's tool-use loop works unchanged.
    """
    if model.startswith("claude"):
        model = DEFAULT_MODEL

    # Convert Anthropic tool schemas → Gemini function declarations. Recursive so
    # array-of-object and nested-object schemas survive the round-trip.
    _TYPE_MAP = {
        "string": "STRING", "integer": "INTEGER", "number": "NUMBER",
        "boolean": "BOOLEAN", "object": "OBJECT", "array": "ARRAY",
    }

    def _convert_schema(pdef: dict) -> dict:
        gtype = _TYPE_MAP.get(pdef.get("type", "string"), "STRING")
        out: dict = {"type": gtype}
        if "description" in pdef:
            out["description"] = pdef["description"]
        if "enum" in pdef:
            out["enum"] = pdef["enum"]
        if gtype == "ARRAY":
            items_def = pdef.get("items", {"type": "string"})
            out["items"] = _convert_schema(items_def)
        elif gtype == "OBJECT":
            sub_props: dict = {}
            for sub_name, sub_def in pdef.get("properties", {}).items():
                sub_props[sub_name] = _convert_schema(sub_def)
            out["properties"] = sub_props
            if pdef.get("required"):
                out["required"] = list(pdef["required"])
        return out

    gemini_tools = []
    for tool in tools:
        schema = tool.get("input_schema", {})
        params = _convert_schema({"type": "object", **schema})
        # Root must be OBJECT; ensure it, even if input_schema lacks "type".
        params["type"] = "OBJECT"
        decl: dict = {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": params,
        }
        gemini_tools.append(decl)

    # Convert Anthropic messages → Gemini contents
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        parts = []

        if isinstance(msg["content"], str):
            parts.append({"text": msg["content"]})
        elif isinstance(msg["content"], list):
            for block in msg["content"]:
                if isinstance(block, dict):
                    if block.get("type") == "tool_result":
                        parts.append({"functionResponse": {
                            "name": block.get("tool_use_id", "unknown"),
                            "response": {"result": block.get("content", "")},
                        }})
                    elif block.get("type") == "tool_use":
                        fc_part: dict = {"functionCall": {
                            "name": block.get("name", ""),
                            "args": block.get("input", {}),
                        }}
                        if block.get("thought_signature"):
                            fc_part["thoughtSignature"] = block["thought_signature"]
                        parts.append(fc_part)
                    elif block.get("type") == "text":
                        parts.append({"text": block.get("text", "")})
                    else:
                        parts.append({"text": str(block)})
                elif hasattr(block, "type"):
                    # Anthropic content block objects from previous responses
                    if block.type == "text":
                        parts.append({"text": block.text})
                    elif block.type == "tool_use":
                        fc_part2: dict = {"functionCall": {
                            "name": block.name,
                            "args": block.input,
                        }}
                        if hasattr(block, "thought_signature") and block.thought_signature:
                            fc_part2["thoughtSignature"] = block.thought_signature
                        parts.append(fc_part2)
                else:
                    parts.append({"text": str(block)})

        if parts:
            contents.append({"role": role, "parts": parts})

    payload: dict = {
        "contents": contents,
        "tools": [{"functionDeclarations": gemini_tools}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    url = f"{_API_BASE}/{model}:generateContent?key={_api_key()}"
    backoff_seconds = [30, 60, 120]
    last_err = None

    for attempt in range(retries):
        try:
            r = httpx.post(url, json=payload, timeout=120)
            if r.status_code in (429, 503):
                wait = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
                logger.warning(f"[gemini] {r.status_code} — retrying in {wait}s")
                time.sleep(wait)
                continue
            if r.status_code == 400:
                logger.error(f"[gemini] 400 error in tool call: {r.text[:500]}")
            r.raise_for_status()
            data = r.json()
            return _parse_tool_response(data)
        except httpx.HTTPStatusError as e:
            last_err = e
            if e.response.status_code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

    # Gemini exhausted — try Groq as 3rd-tier fallback (free, 30 RPM, much
     # fresher quota bucket). Avoids work-item failures when Gemini's free
     # tier is rate-limited.
    try:
        from utils import groq_client
        if groq_client.is_available():
            logger.warning("[gemini] retries exhausted — falling back to Groq")
            return groq_client.ask_with_tools(
                messages=messages, tools=tools, system=system,
                max_tokens=max_tokens, retries=retries,
            )
    except Exception as groq_err:
        logger.error(f"[gemini] Groq fallback also failed: {groq_err}")

    raise RuntimeError(f"Gemini tool call failed after {retries} retries: {last_err}")


class _FakeBlock:
    """Mimics an Anthropic content block (TextBlock or ToolUseBlock)."""
    def __init__(self, block_type, **kwargs):
        self.type = block_type
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeUsage:
    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0


class _FakeMessage:
    """Mimics anthropic.types.Message so callers don't need to change."""
    def __init__(self, content, stop_reason, usage=None):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage or _FakeUsage()


def _parse_tool_response(data: dict) -> _FakeMessage:
    """Convert Gemini generateContent response to Anthropic Message shape."""
    candidates = data.get("candidates", [])
    if not candidates:
        reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
        return _FakeMessage(
            content=[_FakeBlock("text", text=f"Gemini blocked: {reason}")],
            stop_reason="end_turn",
        )

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    finish = candidate.get("finishReason", "STOP")

    content_blocks = []
    has_tool_call = False

    for part in parts:
        if "functionCall" in part:
            has_tool_call = True
            fc = part["functionCall"]
            import uuid
            content_blocks.append(_FakeBlock(
                "tool_use",
                id=fc.get("id", f"gemini_{uuid.uuid4().hex[:8]}"),
                name=fc.get("name", ""),
                input=dict(fc.get("args", {})),
                thought_signature=part.get("thoughtSignature"),
            ))
        elif "text" in part:
            content_blocks.append(_FakeBlock("text", text=part["text"]))

    if not content_blocks:
        content_blocks.append(_FakeBlock("text", text=""))

    stop_reason = "tool_use" if has_tool_call else "end_turn"

    # Extract usage if available
    usage = _FakeUsage()
    usage_meta = data.get("usageMetadata", {})
    usage.input_tokens = usage_meta.get("promptTokenCount", 0)
    usage.output_tokens = usage_meta.get("candidatesTokenCount", 0)

    return _FakeMessage(content=content_blocks, stop_reason=stop_reason, usage=usage)


def _post(model: str, payload: dict, retries: int) -> str:
    """POST to Gemini's generateContent endpoint with retry on 429/5xx.

    Backoff schedule: 30s, 60s, 120s — Gemini free tier is 15 RPM and the cool-down
    needs to be substantial enough to actually clear the bucket.
    """
    url = f"{_API_BASE}/{model}:generateContent?key={_api_key()}"
    backoff_seconds = [30, 60, 120, 240]
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = httpx.post(url, json=payload, timeout=120)
            if r.status_code in (429, 503):
                wait = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
                logger.warning(f"[gemini] {r.status_code} — retrying in {wait}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            try:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                try:
                    from utils import cost_tracker
                    um = data.get("usageMetadata", {}) or {}
                    cost_tracker.record(
                        "gemini",
                        tokens_in=um.get("promptTokenCount", 0) or 0,
                        tokens_out=um.get("candidatesTokenCount", 0) or 0,
                        call_type="synthesis",
                        model=model,
                    )
                except Exception:
                    pass
                return text
            except (KeyError, IndexError) as e:
                # Sometimes Gemini returns a finishReason instead of content (safety filter, etc.)
                reason = data.get("candidates", [{}])[0].get("finishReason", "unknown")
                raise RuntimeError(f"Gemini returned no content (finishReason={reason}): {e}")
        except httpx.HTTPStatusError as e:
            last_err = e
            if e.response.status_code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"Gemini call failed after {retries} retries: {last_err}")

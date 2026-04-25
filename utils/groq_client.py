"""Groq client — free Llama 3.1 inference for Prism agents.

Groq free tier: 14,400 RPD, 30 RPM for Llama 3.1 70B.
No tool-use needed — we use structured prompts and JSON parsing instead.
This is the primary provider for cost-efficient agent synthesis.
"""
from __future__ import annotations

import json
import logging
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama-3.3-70b-versatile"
FAST_MODEL = "llama-3.1-8b-instant"

_API_BASE = "https://api.groq.com/openai/v1/chat/completions"


def _record(data: dict, model: str, call_type: str) -> None:
    """Persist a cost_ledger row. Fail-silent."""
    try:
        from utils import cost_tracker
        usage = data.get("usage", {}) or {}
        cost_tracker.record(
            "groq",
            tokens_in=usage.get("prompt_tokens", 0) or 0,
            tokens_out=usage.get("completion_tokens", 0) or 0,
            call_type=call_type,
            model=model,
        )
    except Exception:
        pass


def _api_key() -> str:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError(
            "GROQ_API_KEY not set. Get one free at https://console.groq.com/keys"
        )
    return key


def synthesize(
    prompt: str,
    max_tokens: int = 4096,
    model: str = DEFAULT_MODEL,
    system: str = "",
    retries: int = 3,
) -> str:
    """Call Groq for text synthesis. Returns the response text.

    Use this for the heavy lifting — analyzing raw research data
    and producing structured findings. Free and fast.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }

    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }

    backoff = [5, 15, 30]
    for attempt in range(retries):
        try:
            r = httpx.post(_API_BASE, json=payload, headers=headers, timeout=60)
            if r.status_code == 429:
                wait = backoff[min(attempt, len(backoff) - 1)]
                logger.warning(f"[groq] 429 rate limit — retrying in {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            _record(data, model, "synthesis")
            return data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

    raise RuntimeError(f"Groq call failed after {retries} retries")


def synthesize_json(
    prompt: str,
    max_tokens: int = 4096,
    model: str = DEFAULT_MODEL,
    system: str = "",
) -> dict | list:
    """Call Groq and parse the response as JSON.

    Adds JSON instruction to the prompt and handles parsing.
    """
    full_prompt = f"{prompt}\n\nRespond with ONLY valid JSON, no other text."

    text = synthesize(full_prompt, max_tokens=max_tokens, model=model, system=system)

    # Extract JSON from response (handle markdown code blocks)
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    return json.loads(text)


def is_available() -> bool:
    """Check if Groq API key is configured."""
    return bool(os.environ.get("GROQ_API_KEY"))


# ---------------------------------------------------------------------------
# Tool-use support — wired in v0.15.3 as the 3rd-tier LLM fallback.
#
# The agent loop calls claude_client.ask_with_tools(); on Gemini exhaustion it
# now falls through to this. Groq Llama 3.3 70B exposes OpenAI-style function
# calling; we translate Anthropic-format tool schemas + messages in/out so the
# caller's tool-use loop is unchanged. Output is wrapped in the same _FakeMessage
# shape that gemini_client uses (imported from there so there's one source of
# truth for Anthropic-compat shims).
# ---------------------------------------------------------------------------


def ask_with_tools(
    messages: list[dict],
    tools: list[dict],
    system: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    retries: int = 3,
):
    """Call Groq with tool/function definitions; returns an Anthropic-compatible Message.

    Used as the 3rd-tier fallback after Claude+Gemini both fail. Free tier
    is 30 RPM / 14,400 RPD on Llama 3.3 70B — substantially fresher than the
    Gemini bucket, which is why this path exists.
    """
    from utils.gemini_client import _FakeBlock, _FakeMessage, _FakeUsage  # type: ignore

    if model.startswith(("claude", "gemini")):
        model = DEFAULT_MODEL

    # ---- Translate Anthropic tool schemas → OpenAI function format ----
    openai_tools = []
    for tool in tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        })

    # ---- Translate Anthropic messages → OpenAI chat format ----
    # Anthropic content is either a string or a list of {type, ...} blocks.
    # OpenAI chat needs: {role, content} for text, {role:assistant, tool_calls:[...]}
    # for tool calls, and {role:tool, tool_call_id, content} for tool results.
    openai_messages = []
    if system:
        openai_messages.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            openai_messages.append({"role": role, "content": content})
            continue

        # List form — split into text/tool_use/tool_result
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        tool_results: list[dict] = []
        for block in content:
            btype = (block.get("type") if isinstance(block, dict) else getattr(block, "type", None))
            if btype == "text":
                txt = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
                if txt:
                    text_parts.append(txt)
            elif btype == "tool_use":
                bid = block.get("id") if isinstance(block, dict) else getattr(block, "id", "")
                bname = block.get("name") if isinstance(block, dict) else getattr(block, "name", "")
                binput = block.get("input") if isinstance(block, dict) else getattr(block, "input", {})
                tool_calls.append({
                    "id": bid,
                    "type": "function",
                    "function": {"name": bname, "arguments": json.dumps(binput or {})},
                })
            elif btype == "tool_result":
                tuid = block.get("tool_use_id") if isinstance(block, dict) else getattr(block, "tool_use_id", "")
                tcontent = block.get("content") if isinstance(block, dict) else getattr(block, "content", "")
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tuid,
                    "content": tcontent if isinstance(tcontent, str) else json.dumps(tcontent),
                })

        if role == "assistant":
            asst_msg: dict = {"role": "assistant"}
            asst_msg["content"] = "\n".join(text_parts) if text_parts else None
            if tool_calls:
                asst_msg["tool_calls"] = tool_calls
            openai_messages.append(asst_msg)
        elif role == "user":
            # User messages with tool_results expand to one tool message per result
            if tool_results:
                openai_messages.extend(tool_results)
                if text_parts:
                    openai_messages.append({"role": "user", "content": "\n".join(text_parts)})
            else:
                openai_messages.append({"role": "user", "content": "\n".join(text_parts)})

    payload = {
        "model": model,
        "messages": openai_messages,
        "tools": openai_tools,
        "tool_choice": "auto",
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }

    backoff = [5, 15, 30]
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = httpx.post(_API_BASE, json=payload, headers=headers, timeout=60)
            if r.status_code == 429:
                wait = backoff[min(attempt, len(backoff) - 1)]
                logger.warning(f"[groq] 429 — retrying in {wait}s (attempt {attempt+1}/{retries})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            _record(data, model, "tool_use")
            return _wrap_groq_response(data, _FakeBlock, _FakeMessage, _FakeUsage)
        except httpx.HTTPStatusError as e:
            last_err = e
            if e.response.status_code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

    raise RuntimeError(f"Groq tool call failed after {retries} retries: {last_err}")


def _wrap_groq_response(data: dict, _FakeBlock, _FakeMessage, _FakeUsage):
    """Translate OpenAI-style response → Anthropic Message-shape via the gemini_client shims."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    finish = choice.get("finish_reason", "stop")

    content_blocks = []
    text = msg.get("content") or ""
    if text:
        content_blocks.append(_FakeBlock("text", text=text))

    has_tool_call = False
    for tc in msg.get("tool_calls") or []:
        has_tool_call = True
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        content_blocks.append(_FakeBlock(
            "tool_use",
            id=tc.get("id", ""),
            name=fn.get("name", ""),
            input=args,
        ))

    if not content_blocks:
        content_blocks.append(_FakeBlock("text", text=""))

    stop_reason = "tool_use" if has_tool_call else "end_turn"

    usage = _FakeUsage()
    u = data.get("usage") or {}
    usage.input_tokens = u.get("prompt_tokens", 0) or 0
    usage.output_tokens = u.get("completion_tokens", 0) or 0

    return _FakeMessage(content=content_blocks, stop_reason=stop_reason, usage=usage)

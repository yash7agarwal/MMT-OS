from __future__ import annotations

import os
import time

import anthropic
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-4-6"
FAST_MODEL = "claude-haiku-4-5-20251001"

_client: anthropic.Anthropic | None = None


def _provider() -> str:
    """Read LLM_PROVIDER env var. Defaults to 'claude'. Set to 'gemini' to use Google Gemini."""
    return os.environ.get("LLM_PROVIDER", "claude").lower()


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Add it to your .env file."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def ask(
    prompt: str,
    max_tokens: int = 1024,
    model: str = DEFAULT_MODEL,
    system: str = "",
    retries: int = 3,
) -> str:
    """Call the configured LLM provider and return the text response. Retries on transient errors."""
    if _provider() == "gemini":
        from utils import gemini_client
        return gemini_client.ask(
            prompt=prompt, max_tokens=max_tokens, model=model, system=system, retries=retries
        )

    messages = [{"role": "user", "content": prompt}]
    kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system

    for attempt in range(retries):
        try:
            resp = _get_client().messages.create(**kwargs)
            return resp.content[0].text
        except anthropic.RateLimitError:
            time.sleep(2 ** attempt * 5)
        except (anthropic.BadRequestError, anthropic.APIStatusError) as e:
            err_str = str(e).lower()
            if "credit balance" in err_str or "usage limits" in err_str or "billing" in err_str:
                import logging
                logging.getLogger(__name__).warning("[claude] Credit limit — falling back to Gemini")
                from utils import gemini_client
                return gemini_client.ask(prompt=prompt, max_tokens=max_tokens, model=model, system=system, retries=retries)
            if hasattr(e, 'status_code') and e.status_code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
            else:
                raise
    raise RuntimeError(f"Claude call failed after {retries} retries")


def ask_fast(prompt: str, max_tokens: int = 512) -> str:
    """Use the fast/cheap model for low-stakes tasks. Routes via ask() so provider switch applies."""
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
    """Send an image + text prompt to the configured vision provider. Returns text response.

    Uses FAST_MODEL by default for speed in navigation loops.
    Pass model=DEFAULT_MODEL for higher-accuracy verification calls.
    Routes to Gemini if LLM_PROVIDER=gemini in .env.
    """
    if _provider() == "gemini":
        from utils import gemini_client
        return gemini_client.ask_vision(
            prompt=prompt,
            image_bytes=image_bytes,
            media_type=media_type,
            max_tokens=max_tokens,
            model=model,
            system=system,
            retries=retries,
        )

    import base64

    img_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": img_b64},
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]
    kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system

    for attempt in range(retries):
        try:
            resp = _get_client().messages.create(**kwargs)
            return resp.content[0].text
        except anthropic.RateLimitError:
            time.sleep(2 ** attempt * 5)
        except anthropic.APIStatusError as e:
            if e.status_code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
            else:
                raise
    raise RuntimeError(f"Claude vision call failed after {retries} retries")


def ask_with_tools(
    messages: list[dict],
    tools: list[dict],
    system: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    retries: int = 3,
) -> anthropic.types.Message:
    """
    Call Claude with tool definitions. Returns the full Message object.
    Used by subagents that need to inspect tool_use blocks in the response.

    Auto-falls back to Gemini when Claude hits billing limits (400 with
    'usage limits' message) or is unavailable. The Gemini response is
    wrapped in a compatible object so callers don't need to change.
    """
    if _provider() == "gemini":
        from utils import gemini_client
        return gemini_client.ask_with_tools(
            messages=messages, tools=tools, system=system,
            model=model, max_tokens=max_tokens, retries=retries,
        )

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "tools": tools,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system

    try:
        return _get_client().messages.create(**kwargs)
    except (anthropic.BadRequestError, anthropic.AuthenticationError) as e:
        err_str = str(e).lower()
        if "credit balance" in err_str or "usage limits" in err_str or "billing" in err_str:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.warning("[claude] Credit/billing limit — switching to Gemini")
            try:
                from utils import gemini_client
                return gemini_client.ask_with_tools(
                    messages=messages, tools=tools, system=system,
                    model=model, max_tokens=max_tokens, retries=retries,
                )
            except Exception as gemini_err:
                _logger.error(f"[claude] Gemini fallback ALSO failed: {gemini_err}", exc_info=True)
                raise gemini_err
        raise
    except anthropic.RateLimitError:
        # Rate limit (not billing) — retry with backoff then fall back
        for attempt in range(retries - 1):
            time.sleep(2 ** attempt * 5)
            try:
                return _get_client().messages.create(**kwargs)
            except anthropic.RateLimitError:
                continue
            except (anthropic.BadRequestError, anthropic.AuthenticationError) as e2:
                err_str2 = str(e2).lower()
                if "credit balance" in err_str2 or "usage limits" in err_str2:
                    from utils import gemini_client
                    return gemini_client.ask_with_tools(
                        messages=messages, tools=tools, system=system,
                        model=model, max_tokens=max_tokens, retries=retries,
                    )
                raise
        # All retries exhausted — fall back to Gemini
        import logging
        logging.getLogger(__name__).warning("[claude] Rate limit exhausted — falling back to Gemini")
        from utils import gemini_client
        return gemini_client.ask_with_tools(
            messages=messages, tools=tools, system=system,
            model=model, max_tokens=max_tokens, retries=retries,
        )
    except anthropic.APIStatusError as e:
        if e.status_code >= 500:
            from utils import gemini_client
            return gemini_client.ask_with_tools(
                messages=messages, tools=tools, system=system,
                model=model, max_tokens=max_tokens, retries=retries,
            )
        raise

"""
agent/llm.py — LLM provider abstraction.
Priority: Ollama (local) → Gemini Flash (free API) → OpenRouter (paid fallback).
"""

import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TIMEOUT = 30  # seconds

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
GEMINI_TIMEOUT = 60  # seconds

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "anthropic/claude-haiku-4.5"
OPENROUTER_TIMEOUT = 60  # seconds

import time as _time

_last_gemini_call: float = 0.0  # epoch seconds of last successful Gemini call
_GEMINI_MIN_INTERVAL: float = 4.1  # seconds; 15 RPM = 4s + 0.1s buffer


def call_llm(prompt: str, system: str = "") -> str:
    """
    Call LLM with prompt. Returns plain text response.
    Priority: Ollama → Gemini Flash → OpenRouter.
    """
    try:
        return _call_ollama(prompt, system)
    except Exception as e:
        logger.warning("Ollama unavailable (%s), falling back to Gemini", e)
    try:
        return _call_gemini(prompt, system)
    except Exception as e:
        logger.warning("Gemini unavailable (%s), falling back to OpenRouter", e)
        return _call_openrouter(prompt, system)


def _call_ollama(prompt: str, system: str) -> str:
    """Call local Ollama instance."""
    try:
        with open(os.path.join(os.path.dirname(__file__), "..", "config.json")) as f:
            cfg = json.load(f).get("scoring", {})
            model = cfg.get("ollama_model", "llama3.2:3b")
            timeout = cfg.get("ollama_timeout", 60)
    except Exception:
        model = "llama3.2:3b"
        timeout = 60

    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    resp = requests.post(
        OLLAMA_URL,
        json={"model": model, "prompt": full_prompt, "stream": False},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def _call_gemini(prompt: str, system: str) -> str:
    """Call Gemini Flash via Google AI API (free tier, 15 RPM)."""
    global _last_gemini_call
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    # Respect 15 RPM: sleep only the remaining gap since the last call
    elapsed = _time.time() - _last_gemini_call
    wait = _GEMINI_MIN_INTERVAL - elapsed
    if wait > 0:
        _time.sleep(wait)

    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    # Retry on 429 (rate-limit) with exponential backoff. Honours the
    # Retry-After header when the API supplies one. A persistent 429 (daily
    # quota exhausted) still raises after the final attempt, at which point the
    # caller falls back to keyword scoring.
    max_attempts = 3
    resp = None
    for attempt in range(max_attempts):
        resp = requests.post(
            f"{GEMINI_URL}?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": full_prompt}]}]},
            timeout=GEMINI_TIMEOUT,
        )
        if resp.status_code != 429:
            break
        if attempt == max_attempts - 1:
            break
        retry_after = resp.headers.get("Retry-After")
        try:
            backoff = float(retry_after) if retry_after else 2.0 * (2 ** attempt)
        except ValueError:
            backoff = 2.0 * (2 ** attempt)
        logger.warning(
            "Gemini 429 (attempt %d/%d), backing off %.1fs",
            attempt + 1, max_attempts, backoff,
        )
        _time.sleep(backoff)

    resp.raise_for_status()
    data = resp.json()
    try:
        result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        _last_gemini_call = _time.time()  # record AFTER successful response
        return result
    except (KeyError, IndexError) as e:
        raise ValueError(f"Unexpected Gemini response: {data}") from e


def _call_openrouter(prompt: str, system: str) -> str:
    """Call OpenRouter API."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set and Ollama unavailable")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": OPENROUTER_MODEL, "messages": messages},
        timeout=OPENROUTER_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise ValueError(f"OpenRouter returned no choices: {data}")
    return (choices[0].get("message") or {}).get("content", "").strip()


def call_llm_json_fast(prompt: str, system: str = "") -> dict:
    """Call Gemini directly (skips Ollama). Use for batch/research tasks where speed matters."""
    try:
        text = _call_gemini(prompt, system)
    except Exception as e:
        logger.warning("Gemini unavailable (%s), falling back to OpenRouter", e)
        text = _call_openrouter(prompt, system)
    start = text.find('{')
    if start >= 0:
        try:
            result, _ = json.JSONDecoder().raw_decode(text, start)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    logger.warning("LLM did not return valid JSON: %s", text[:200])
    return {}


def call_llm_json(prompt: str, system: str = "") -> dict:
    """
    Call LLM and parse JSON from response.
    Extracts first JSON object found in response text.
    Returns empty dict on parse failure.
    """
    text = call_llm(prompt, system)
    # Find the first '{' and use raw_decode to correctly parse the first JSON object,
    # handling nesting and stopping at the right closing brace.
    start = text.find('{')
    if start >= 0:
        try:
            result, _ = json.JSONDecoder().raw_decode(text, start)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    logger.warning("LLM did not return valid JSON: %s", text[:200])
    return {}

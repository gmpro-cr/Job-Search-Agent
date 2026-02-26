"""
agent/llm.py — LLM provider abstraction.
Tries Ollama first; falls back to OpenRouter on error or timeout.
"""

import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TIMEOUT = 30  # seconds

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "anthropic/claude-haiku-4-5-20251001"
OPENROUTER_TIMEOUT = 60  # seconds


def call_llm(prompt: str, system: str = "") -> str:
    """
    Call LLM with prompt. Returns plain text response.
    Tries Ollama first; falls back to OpenRouter.
    """
    try:
        return _call_ollama(prompt, system)
    except Exception as e:
        logger.warning("Ollama unavailable (%s), falling back to OpenRouter", e)
        return _call_openrouter(prompt, system)


def _call_ollama(prompt: str, system: str) -> str:
    """Call local Ollama instance."""
    # Read model from config if available
    try:
        with open(os.path.join(os.path.dirname(__file__), "..", "config.json")) as f:
            model = json.load(f).get("scoring", {}).get("ollama_model", "llama3.2:3b")
    except Exception:
        model = "llama3.2:3b"

    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    resp = requests.post(
        OLLAMA_URL,
        json={"model": model, "prompt": full_prompt, "stream": False},
        timeout=OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


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

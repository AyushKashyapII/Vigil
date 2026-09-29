"""Thin client for Groq's OpenAI-compatible chat completions endpoint.

Plain urllib, not the openai/requests packages -- brain is deliberately
stdlib-only (see brain/Dockerfile's comment on this). One call, no
streaming, no conversation state: this is a single structured request for
a rewritten query, not a chat.
"""

import json
import os
import urllib.error
import urllib.request

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")


def chat(system_prompt: str, user_prompt: str) -> str | None:
    """Sends a single-turn request, returns the raw response text, or
    None if there's no API key configured or the call fails for any
    reason -- fails closed, same as everything else upstream that can't
    act without something it needs.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None

    body = json.dumps({
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
    }).encode()

    request = urllib.request.Request(
        GROQ_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Groq sits behind Cloudflare, which blocks urllib's default
            # "Python-urllib/x.y" User-Agent as bot traffic (HTTP 403,
            # Cloudflare error 1010) before the request ever reaches Groq.
            "User-Agent": "vigil-brain/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None

    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None

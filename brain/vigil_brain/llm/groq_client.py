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

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from vigil_brain.telemetry import tracer

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")


def chat(system_prompt: str, user_prompt: str) -> str | None:
    """Sends a single-turn request, returns the raw response text, or
    None if there's no API key configured or the call fails for any
    reason -- fails closed, same as everything else upstream that can't
    act without something it needs.
    """
    # Span name and gen_ai.* attributes follow OTel's GenAI semantic
    # conventions ("{operation} {model}"), so LLM-aware backends can
    # recognise the call. Prompt/response TEXT is deliberately not
    # recorded: prompts can carry user data, and telemetry backends are
    # usually not where you want that. Lengths are enough to debug with.
    with tracer.start_as_current_span(
        f"chat {GROQ_MODEL}",
        kind=trace.SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "groq",
            "gen_ai.request.model": GROQ_MODEL,
            "gen_ai.request.temperature": 0,
            "vigil.llm.prompt_chars": len(user_prompt),
        },
    ) as span:
        return _chat(span, system_prompt, user_prompt)


def _chat(span, system_prompt: str, user_prompt: str) -> str | None:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        # Not an error -- a supported "LLM disabled" configuration.
        span.set_attribute("vigil.llm.skipped", "no_api_key")
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
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        # Still fails closed (returns None) -- but now the failure is
        # visible: which one, and for HTTP errors, which status.
        # (HTTPError is a subclass of URLError, so check it first.)
        if isinstance(e, urllib.error.HTTPError):
            span.set_attribute("http.response.status_code", e.code)
        span.record_exception(e)
        span.set_status(Status(StatusCode.ERROR, type(e).__name__))
        return None

    usage = payload.get("usage") or {}
    if "prompt_tokens" in usage:
        span.set_attribute("gen_ai.usage.input_tokens", usage["prompt_tokens"])
    if "completion_tokens" in usage:
        span.set_attribute("gen_ai.usage.output_tokens", usage["completion_tokens"])
    if payload.get("model"):
        span.set_attribute("gen_ai.response.model", payload["model"])

    try:
        choice = payload["choices"][0]
        if choice.get("finish_reason"):
            span.set_attribute("gen_ai.response.finish_reasons", [choice["finish_reason"]])
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        span.record_exception(e)
        span.set_status(Status(StatusCode.ERROR, "unexpected response shape"))
        return None
    span.set_attribute("vigil.llm.response_chars", len(content or ""))
    return content

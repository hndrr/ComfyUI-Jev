"""The only outbound request path in this extension."""

import asyncio
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp

from .semantics import candidate_strings, dumps, loads


ENDPOINT = "https://api.typesafe.ai/v1/systemone"
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
CHAT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def connection(provider, model):
    if provider == "typesafe":
        return ENDPOINT, "TYPESAFE_API_KEY", model
    if provider == "openrouter":
        aliases = {"jev-latest": "~typesafe/jev-latest", "jev-1.13.0": "typesafe/jev-1.13"}
        if model == "jev-preview":
            raise ValueError("jev-preview has no verified OpenRouter alias; select jev-latest or custom with an OpenRouter model ID")
        return OPENROUTER_ENDPOINT, "OPENROUTER_API_KEY", aliases.get(model, model)
    raise ValueError(f"Unknown Jev provider: {provider}")


def retry_delay(header, attempt):
    if header:
        try:
            seconds = float(header)
        except ValueError:
            try:
                date = parsedate_to_datetime(header)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                seconds = (date - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return 2 ** attempt
        if 0 <= seconds < float("inf"):
            return seconds
    return 2 ** attempt


async def evaluate(state, questions, model, provider="typesafe", api_key=""):
    endpoint, key_name, model = connection(provider, model)
    if not questions:
        return {"model": model, "answers": {}, "usage": {"input_tokens": 0, "output_tokens": 0}}
    return await _post_json(endpoint, {"model": model, "state": state, "questions": questions},
                            key_name, api_key, "Jev", provider)


async def generate(prompt, system, model, temperature=0.7, max_tokens=2048, candidate_count=0, api_key=""):
    """Generate ordinary text, or complete candidates using a private output schema."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("OpenRouter Text: prompt cannot be empty")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("OpenRouter Text: model ID cannot be empty")
    if type(candidate_count) is not int or not (candidate_count == 0 or 2 <= candidate_count <= 100):
        raise ValueError("OpenRouter Text: candidate_count must be 2–100, or 0 for text")
    messages = []
    if system.strip():
        messages.append({"role": "system", "content": system})
    if candidate_count:
        messages.append({"role": "system", "content": (
            f"Generate exactly {candidate_count} distinct candidates satisfying the user's request. "
            "Each candidate must be complete and usable on its own, with no option number or commentary. "
            "Preserve any line breaks within each candidate. Return them in the candidates array."
        )})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": model.strip(), "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": False}
    if candidate_count:
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "creative_candidates", "strict": True, "schema": {
                "type": "object", "properties": {
                    "candidates": {"type": "array", "items": {"type": "string"}},
                }, "required": ["candidates"], "additionalProperties": False,
            },
        }}
        payload["provider"] = {"require_parameters": True}
    return await _post_json(CHAT_ENDPOINT, payload, "OPENROUTER_API_KEY", api_key, "OpenRouter Text", "openrouter")


def generated_text(response, candidate_count=0):
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("OpenRouter Text: response is missing a completion")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("refusal"):
        raise ValueError("OpenRouter Text: model refused or did not return a text message")
    if choice.get("finish_reason") != "stop":
        raise ValueError("OpenRouter Text: generation did not finish normally; check max_tokens and model support")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("OpenRouter Text: model returned no text")
    if not candidate_count:
        return content
    parsed = loads(content, "OpenRouter candidates")
    if not isinstance(parsed, dict) or set(parsed) != {"candidates"}:
        raise ValueError("OpenRouter Text: response must contain a candidates array")
    candidates = candidate_strings(parsed["candidates"], "OpenRouter candidates")
    if len(candidates) != candidate_count:
        raise ValueError(f"OpenRouter Text: expected {candidate_count} candidates, received {len(candidates)}")
    return dumps(candidates)


async def _post_json(endpoint, payload, key_name, api_key, label, provider):
    api_key = api_key.strip() or os.environ.get(key_name, "").strip()
    if not api_key:
        raise ValueError(f"Enter api_key on the node or set {key_name} in the ComfyUI process environment and restart ComfyUI")
    timeout = aiohttp.ClientTimeout(total=60)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for attempt in range(3):
                async with session.post(endpoint, json=payload,
                                        headers={"Authorization": f"Bearer {api_key}"}, allow_redirects=False) as response:
                    if response.status in (429, 529) and attempt < 2:
                        delay = retry_delay(response.headers.get("Retry-After"), attempt)
                        await response.read()
                    elif response.status != 200:
                        messages = {401: f"Invalid API key for {provider}", 402: "Insufficient credits",
                                    400: "Invalid request; check inputs and model support",
                                    404: "Model or endpoint not found; check model ID",
                                    422: "Rejected inputs; check model support and API limits",
                                    429: "Rate limit exceeded", 529: "Provider temporarily overloaded"}
                        raise RuntimeError(f"{label} HTTP {response.status} ({provider}): {messages.get(response.status, 'Request failed')}")
                    else:
                        result = loads(await response.text(), f"{label} response")
                        if not isinstance(result, dict):
                            raise ValueError(f"{label} response must be a JSON object")
                        return result
                await asyncio.sleep(delay)
    except asyncio.TimeoutError:
        raise RuntimeError(f"{label} request timed out after 60 seconds") from None
    except aiohttp.ClientError as error:
        raise RuntimeError(f"{label} connection failed ({type(error).__name__})") from None

"""The only outbound request path in this extension."""

import asyncio
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp

from .semantics import loads


ENDPOINT = "https://api.typesafe.ai/v1/systemone"
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"


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
    api_key = api_key.strip() or os.environ.get(key_name, "").strip()
    if not api_key:
        raise ValueError(f"Enter api_key on Jev Interpret or set {key_name} in the ComfyUI process environment and restart ComfyUI")
    timeout = aiohttp.ClientTimeout(total=60)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for attempt in range(3):
                async with session.post(endpoint, json={"model": model, "state": state, "questions": questions},
                                        headers={"Authorization": f"Bearer {api_key}"}, allow_redirects=False) as response:
                    if response.status in (429, 529) and attempt < 2:
                        delay = retry_delay(response.headers.get("Retry-After"), attempt)
                        await response.read()
                    elif response.status != 200:
                        messages = {401: f"Invalid API key for {provider}", 402: "Insufficient credits",
                                    400: "Invalid request; check state, schema and model ID",
                                    404: "Model or endpoint not found; check model ID",
                                    422: "Rejected state or questions; check schema and API limits",
                                    429: "Rate limit exceeded", 529: "Provider temporarily overloaded"}
                        raise RuntimeError(f"Jev HTTP {response.status} ({provider}): {messages.get(response.status, 'Request failed')}")
                    else:
                        payload = loads(await response.text(), "Jev response")
                        if not isinstance(payload, dict):
                            raise ValueError("Jev response must be a JSON object")
                        return payload
                await asyncio.sleep(delay)
    except asyncio.TimeoutError:
        raise RuntimeError("Jev request timed out after 60 seconds") from None
    except aiohttp.ClientError as error:
        raise RuntimeError(f"Jev connection failed ({type(error).__name__})") from None

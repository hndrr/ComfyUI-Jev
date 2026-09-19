"""The only outbound request path in this extension."""

import asyncio
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp

from .semantics import loads


ENDPOINT = "https://api.typesafe.ai/v1/systemone"


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


async def evaluate(state, questions, model):
    if not questions:
        return {"model": model, "answers": {}, "usage": {"input_tokens": 0, "output_tokens": 0}}
    api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Set TYPESAFE_API_KEY in the ComfyUI process environment and restart ComfyUI")
    timeout = aiohttp.ClientTimeout(total=60)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for attempt in range(3):
                async with session.post(ENDPOINT, json={"model": model, "state": state, "questions": questions},
                                        headers={"Authorization": f"Bearer {api_key}"}, allow_redirects=False) as response:
                    if response.status in (429, 529) and attempt < 2:
                        delay = retry_delay(response.headers.get("Retry-After"), attempt)
                        await response.read()
                    elif response.status != 200:
                        messages = {401: "Invalid TypeSafe API key", 422: "TypeSafe rejected the state or questions; check the schema and API limits",
                                    429: "TypeSafe rate limit exceeded", 529: "TypeSafe is temporarily overloaded"}
                        raise RuntimeError(f"Jev HTTP {response.status}: {messages.get(response.status, 'TypeSafe request failed')}")
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

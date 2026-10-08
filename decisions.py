"""OpenRouter Decisions wire contract and limits, independent of ComfyUI UI code."""

import base64
import binascii
from io import BytesIO
import json
import warnings

from .media import Context


# https://openrouter.ai/docs/guides/community/multimodal-decisions
MODELS = {
    "openai/gpt-6-luna-decisions": {"images": 128, "questions": 200, "provider": "openai"},
    "cloudflare/clef": {"images": 4, "questions": 64, "provider": "cloudflare"},
    "cloudflare/clef-flash": {"images": 4, "questions": 64, "provider": "cloudflare"},
}


def _image(part):
    from PIL import Image, UnidentifiedImageError

    url = part["image_url"]["url"]
    header, separator, encoded = url.partition(",")
    formats = {"data:image/png;base64": "PNG", "data:image/jpeg;base64": "JPEG", "data:image/webp;base64": "WEBP"}
    if not separator or header not in formats:
        raise ValueError("Decisions images require PNG/JPEG/WebP base64 data URLs; remote URLs are not fetched. Connect a ComfyUI Load Image node instead.")
    try:
        data = base64.b64decode(encoded, validate=True)
        with Image.open(BytesIO(data)) as image:
            if image.format != formats[header]:
                raise ValueError("Image data does not match its declared MIME type")
            image.verify()
    except (binascii.Error, OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ValueError("Invalid or oversized image data URL") from None
    return len(encoded)


def payload(state, questions, model, provider):
    spec = MODELS.get(model)
    if spec and provider != "openrouter":
        raise ValueError(f"{model} requires provider=openrouter")
    image_parts = []
    if isinstance(state, Context):
        items = list(state.parts)
        for part in items:
            if isinstance(part, str):
                continue
            if part.get("type") != "image_url":
                raise ValueError("These Decisions models accept text and images, not native audio/video/files. Connect existing ComfyUI transcription or frame/page extraction nodes.")
            image_parts.append(part)
        if image_parts:
            if provider != "openrouter" or spec is None:
                raise ValueError("Image Decisions require OpenRouter and a supported Luna Decisions, Clef, or Clef Flash model")
            if len(image_parts) > spec["images"]:
                raise ValueError(f"{model} accepts at most {spec['images']} images; select a smaller batch explicitly")
            for part in image_parts:
                _image(part)
            state = items  # Images are DIRECT state items; text items are plain strings.
        else:
            state = "\n".join(items)
    result = {"model": model, "state": state, "questions": questions}
    if spec:
        if len(questions) > spec["questions"]:
            raise ValueError(f"{model} accepts at most {spec['questions']} questions; reduce candidates or shortlist size")
        if spec["provider"] == "cloudflare":
            for question in questions.values():
                count = len(question.get("criteria", []))
                if question.get("type") == "choice" and not 2 <= count <= 255:
                    raise ValueError("Clef choice questions require 2–255 candidates")
                if question.get("type") == "score" and not 2 <= count <= 10:
                    raise ValueError("Clef score questions require 2–10 levels")
            text = state if isinstance(state, str) else "\n".join(p for p in state if isinstance(p, str)) if isinstance(state, list) else json.dumps(state, ensure_ascii=False)
            if len(text.encode("utf-8")) > 2000:
                warnings.warn("The Cloudflare route for Clef reads only about the first 2,000 state text tokens. This context exceeds 2,000 UTF-8 bytes and may be truncated upstream; shorten it or use Luna Decisions. No local truncation is performed.", UserWarning, stacklevel=2)
            if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 4 * 65536:
                raise ValueError("Clef request exceeds the conservative 256 KiB encoded request budget; resize/recompress images with existing ComfyUI nodes")
    if image_parts:
        # The public guide describes these native routes. Cheaper third-party routing
        # returned text-like guesses in the recorded Clef image/control experiment.
        result["provider"] = {"only": [spec["provider"]], "allow_fallbacks": False}
    return result

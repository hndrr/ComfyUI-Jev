"""TypeSafe and OpenRouter wire contracts and limits, independent of ComfyUI UI code."""

import base64
import binascii
from io import BytesIO
import json
import uuid

from .media import Context


# https://openrouter.ai/docs/guides/community/multimodal-decisions
MODELS = {
    "openai/gpt-6-luna-decisions": {"images": 128, "questions": 200, "provider": "openai"},
    "cloudflare/clef": {"images": 4, "questions": 64, "provider": "cloudflare"},
    "cloudflare/clef-flash": {"images": 4, "questions": 64, "provider": "cloudflare"},
}

CLEF_REQUEST_BUDGET = 256 * 1024
# Box support is not implied by image support in the model catalog.
BOX_MODELS = ("typesafe/jev-1.14", "~typesafe/jev-latest")
TYPESAFE_BOX_MODELS = ("jev-1.14.0", "jev-preview")
TYPESAFE_IMAGE_MODELS = (*TYPESAFE_BOX_MODELS, "jev-latest", "jev-1.13.0")
TYPESAFE_REQUEST_BUDGET = 32 * 1024 * 1024


def encode_payload(body):
    """Serialize exactly the bytes used for both the size check and HTTP body."""
    return json.dumps(body).encode("utf-8")


def context_warnings(state, model, provider):
    """Conservative text-length notices for each node result, not token counts."""
    if provider != "openrouter" or MODELS.get(model, {}).get("provider") != "cloudflare":
        return []
    if isinstance(state, Context):
        state = list(state.parts)
    text = state if isinstance(state, str) else "\n".join(p for p in state if isinstance(p, str)) if isinstance(state, list) else json.dumps(state, ensure_ascii=False)
    if len(text.encode("utf-8")) <= 2000:
        return []
    return ["The OpenRouter Decisions guide describes upstream truncation of Clef state text at about 2,000 tokens. This context exceeds the conservative 2,000 UTF-8 byte warning threshold; this is not a token count or proof of truncation. Shorten the context or use Luna Decisions if needed. No local truncation is performed."]


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
        # verify() alone can accept a JPEG with a truncated pixel stream.
        with Image.open(BytesIO(data)) as image:
            image.load()
            info = {"format": image.format.lower(), "data_base64": encoded,
                    "width": image.width, "height": image.height,
                    "size_bytes": len(data), "animated": getattr(image, "is_animated", False)}
    except (binascii.Error, OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ValueError("Invalid or oversized image data URL") from None
    return info


def payload(state, questions, model, provider):
    has_box = any(question.get("type") == "box" for question in questions.values())
    box_models = TYPESAFE_BOX_MODELS if provider == "typesafe" else BOX_MODELS
    if has_box and model not in box_models:
        raise ValueError("Box requires Jev 1.14: typesafe uses jev-1.14.0/jev-preview; openrouter uses typesafe/jev-1.14/~typesafe/jev-latest")
    spec = MODELS.get(model)
    if spec and provider != "openrouter":
        raise ValueError(f"{model} requires provider=openrouter")
    image_parts = []
    assets = {}
    if isinstance(state, Context):
        items = list(state.parts)
        for part in items:
            if isinstance(part, str):
                continue
            if part.get("type") != "image_url":
                raise ValueError("These Decisions models accept text and images, not native audio/video/files. Connect existing ComfyUI transcription or frame/page extraction nodes.")
            image_parts.append(part)
        if image_parts:
            if provider == "typesafe":
                if model not in TYPESAFE_IMAGE_MODELS:
                    raise ValueError("TypeSafe images require a supported Jev model")
                state = []
                for part in items:
                    if isinstance(part, str):
                        state.append(part)
                        continue
                    image = _image(part)
                    if image["format"] not in ("png", "jpeg") or image["animated"]:
                        raise ValueError("TypeSafe images require still PNG or JPEG files")
                    if min(image["width"], image["height"]) < 280 or max(image["width"], image["height"]) > 1568:
                        raise ValueError("TypeSafe images must be 280–1568 pixels on each side; resize before connecting images")
                    if image["size_bytes"] > 16 * 1024 * 1024:
                        raise ValueError("TypeSafe image file exceeds 16 MiB")
                    reference = str(uuid.uuid4())
                    assets[reference] = {"type": "image", "format": image["format"], "data_base64": image["data_base64"]}
                    state.append(reference)
            else:
                from . import model_catalog

                architecture = model_catalog.decision_models.get(model, {})
                supports_images = model in BOX_MODELS or "image" in architecture.get("input_modalities", [])
                if provider != "openrouter" or not supports_images:
                    raise ValueError("Image Decisions require an OpenRouter Decisions model with verified image input support in the catalog")
                if spec and len(image_parts) > spec["images"]:
                    raise ValueError(f"{model} accepts at most {spec['images']} images; select a smaller batch explicitly")
                for part in image_parts:
                    _image(part)
                state = items  # Images are DIRECT state items; text items are plain strings.
        else:
            state = "\n".join(items)
    if has_box and not image_parts:
        raise ValueError("Box requires an image; connect images to Jev Find Box")
    result = {"model": model, "state": state, "questions": questions}
    if assets:
        result["assets"] = assets
        if len(encode_payload(result)) > TYPESAFE_REQUEST_BUDGET:
            raise ValueError("TypeSafe request exceeds 32 MiB; reduce image size or batch count")
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
    if image_parts and spec:
        # The public guide describes these native routes. Cheaper third-party routing
        # returned text-like guesses in the recorded Clef image/control experiment.
        result["provider"] = {"only": [spec["provider"]], "allow_fallbacks": False}
    if spec and spec["provider"] == "cloudflare" and len(encode_payload(result)) > CLEF_REQUEST_BUDGET:
        raise ValueError("Clef request exceeds the local conservative 256 KiB encoded request budget; shorten text or resize/recompress images with existing ComfyUI nodes")
    return result

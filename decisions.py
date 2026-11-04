"""TypeSafe and OpenRouter wire contracts and limits, independent of ComfyUI UI code."""

import base64
import binascii
from io import BytesIO
import json
import uuid

from .media import Context
from . import limits


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
    try:
        return json.dumps(body, allow_nan=False).encode("utf-8")
    except (ValueError, RecursionError):
        raise ValueError("Request requires finite JSON values and supported nesting; flatten nested data") from None


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

    image_url = part.get("image_url")
    if (set(part) != {"type", "image_url"} or not isinstance(image_url, dict)
            or not set(image_url) <= {"url", "detail"} or not isinstance(image_url.get("url"), str)):
        raise ValueError("An image_url part requires a data URL and optional detail")
    if "detail" in image_url and image_url["detail"] not in ("auto", "low", "high"):
        raise ValueError("Image detail must be auto, low, or high")
    url = image_url["url"]
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
    limits.questions(questions, model, provider)
    has_box = any(question.get("type") == "box" for question in questions.values())
    box_models = TYPESAFE_BOX_MODELS if provider == "typesafe" else BOX_MODELS
    if has_box and model not in box_models:
        raise ValueError("Box requires Jev 1.14: typesafe uses jev-1.14.0/jev-preview; openrouter uses typesafe/jev-1.14/~typesafe/jev-latest")
    spec = MODELS.get(model)
    if spec and provider != "openrouter":
        raise ValueError(f"{model} requires provider=openrouter")
    image_parts, image_info, assets = [], [], {}
    if isinstance(state, Context):
        items = list(state.parts)
        for part in items:
            if not isinstance(part, str) and part.get("type") != "image_url":
                raise ValueError("These Decisions models accept text and images, not native audio/video/files. Connect existing ComfyUI transcription or frame/page extraction nodes.")
        state = items if any(not isinstance(part, str) for part in items) else "\n".join(items)

    def convert(part, location):
        if provider == "typesafe":
            if model not in TYPESAFE_IMAGE_MODELS:
                raise ValueError("TypeSafe images require a supported Jev model")
        else:
            from . import model_catalog
            architecture = model_catalog.decision_models.get(model, {})
            if provider != "openrouter" or not (limits.is_jev(model, provider) or "image" in architecture.get("input_modalities", [])):
                raise ValueError("Image Decisions require an OpenRouter Decisions model with verified image input support in the catalog")
            if location != "state_item" and not limits.is_jev(model, provider):
                raise ValueError("Images in structured state, instructions, or criteria require a verified Jev model; other Decisions models only read top-level state images")
        image = _image(part)
        image_parts.append(part)
        image_info.append(image)
        if provider == "typesafe":
            reference = str(uuid.uuid4())
            assets[reference] = {"type": "image", "format": image["format"], "data_base64": image["data_base64"]}
            return reference
        return {"type": "image_url", "image_url": dict(part["image_url"])}

    def transform(value, location="nested", depth=0):
        if depth > 64:
            raise ValueError("Request exceeds 64 nested levels; flatten state/questions")
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                return convert(value, location)
            return {key: transform(item, "nested", depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            return [transform(item, "state_item" if location == "state" else "nested", depth + 1) for item in value]
        return value

    state = transform(state, "state")
    questions = {key: {name: transform(value) if name in ("instructions", "criteria") else value
                       for name, value in question.items()} for key, question in questions.items()}
    if has_box and not image_parts:
        raise ValueError("Box requires an image; connect images to Jev Find Box")
    result = {"model": model, "state": state, "questions": questions}
    limits.images(image_info, model, provider)
    if assets:
        result["assets"] = assets
        if len(encode_payload(result)) > TYPESAFE_REQUEST_BUDGET:
            raise ValueError("TypeSafe request exceeds 32 MiB; reduce image size or batch count")
    if spec:
        if len(questions) > spec["questions"]:
            raise ValueError(f"{model} accepts at most {spec['questions']} questions; reduce candidates or shortlist size")
    if image_parts and spec:
        # The public guide describes these native routes. Cheaper third-party routing
        # returned text-like guesses in the recorded Clef image/control experiment.
        result["provider"] = {"only": [spec["provider"]], "allow_fallbacks": False}
    if spec and spec["provider"] == "cloudflare" and len(encode_payload(result)) > CLEF_REQUEST_BUDGET:
        raise ValueError("Clef request exceeds the local conservative 256 KiB encoded request budget; shorten text or resize/recompress images with existing ComfyUI nodes")
    limits.request(result, len(encode_payload(result)), model, provider, bool(image_parts))
    return result

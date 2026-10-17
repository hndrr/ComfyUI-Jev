"""Documented Decisions limits; unknown models do not inherit another model's caps."""

import math


TYPESAFE_JEV = ("jev-latest", "jev-preview", "jev-1.13.0", "jev-1.14.0")
OPENROUTER_JEV = ("typesafe/jev-1.13", "typesafe/jev-1.14", "~typesafe/jev-latest")
JEV_REQUEST_BYTES = 32 * 1024 * 1024
JEV_IMAGE_BYTES = 16 * 1024 * 1024
LIQUID_REQUEST_BYTES = 4_500_000
# The guide records a successful 7 MB image and failures at 12/16 MB, rather
# than a contractual maximum. Keep a labeled conservative local budget.
MERCURY_IMAGE_BYTES = 8_000_000
IMAGE_COUNTS = {"openai/gpt-6-luna-decisions": 128, "cloudflare/clef": 4,
                "cloudflare/clef-flash": 4, "cloudflare/clef-omni": 4,
                "inception/mercury-decide": 16, "liquid/d1": 8, "liquid/d1-ultra": 8}


def is_jev(model, provider):
    return model in (TYPESAFE_JEV if provider == "typesafe" else OPENROUTER_JEV) if provider in ("typesafe", "openrouter") else False


def questions(questions, model, provider):
    jev = provider == "typesafe" or is_jev(model, provider)
    clef = provider == "openrouter" and model in ("cloudflare/clef", "cloudflare/clef-flash")
    for key, question in questions.items():
        kind, criteria = question.get("type"), question.get("criteria")
        if (jev or clef) and kind in ("choice", "score"):
            minimum, maximum = (2, 10) if kind == "score" else (2 if clef else 1, 255)
            expected = list if kind == "score" else dict
            if not isinstance(criteria, expected) or not minimum <= len(criteria) <= maximum:
                unit = "levels" if kind == "score" else "candidates"
                raise ValueError(f"{model}: question {key} requires {minimum}–{maximum} {kind} {unit}; reduce or split the rubric/candidates")


def images(images, model, provider):
    maximum = IMAGE_COUNTS.get(model) if provider == "openrouter" else None
    if maximum is not None and len(images) > maximum:
        raise ValueError(f"{model} accepts at most {maximum} images; select a smaller batch explicitly")
    for image in images:
        if is_jev(model, provider):
            if image["format"] not in ("png", "jpeg") or image["animated"]:
                raise ValueError(f"{model}: Jev images require still PNG or JPEG files; convert WebP/animation first")
            minimum = 280 if provider == "typesafe" else 256
            width, height = image["width"], image["height"]
            if min(width, height) < minimum or max(width, height) > 1568 or not 78400 <= width * height <= 2458624:
                raise ValueError(f"{model}: images must be {minimum}–1568 pixels per side and 78,400–2,458,624 pixels in area; resize before connecting images")
            if image["size_bytes"] > JEV_IMAGE_BYTES:
                raise ValueError(f"{model}: image file exceeds 16 MiB; resize or recompress it")
        elif provider == "openrouter" and model == "inception/mercury-decide" and image["size_bytes"] > MERCURY_IMAGE_BYTES:
            raise ValueError(f"{model}: image exceeds the conservative local 8 MB budget; resize or recompress it")
    if provider == "openrouter" and model in ("liquid/d1", "liquid/d1-ultra"):
        patches = sum(math.ceil(image["width"] / 32) * math.ceil(image["height"] / 32) for image in images)
        if patches > 10000:
            raise ValueError(f"{model}: images exceed 10,000 total 32×32 patches; resize or reduce the batch")


def _structure(value):
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, (dict, list)):
            if depth >= 64:
                raise ValueError("Jev JSON exceeds 64 nested levels; flatten the state or question")
            values = item.values() if isinstance(item, dict) else item
            pending.extend((child, depth + 1) for child in values)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Jev JSON requires finite numbers; encode exact large values as strings")


def request(body, size, model, provider, has_images=False):
    if provider == "typesafe" or is_jev(model, provider):
        if size > JEV_REQUEST_BYTES:
            raise ValueError(f"{model}: request exceeds 32 MiB; shorten text/questions or reduce image size/batch")
        _structure(body["state"])
        for question in body["questions"].values():
            _structure(question.get("instructions"))
            _structure(question.get("criteria"))
    if has_images and provider == "openrouter" and model in ("liquid/d1", "liquid/d1-ultra") and size > LIQUID_REQUEST_BYTES:
        raise ValueError(f"{model}: request exceeds 4.5 MB; resize or reduce the image batch")

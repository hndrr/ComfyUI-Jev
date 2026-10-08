import logging
from pathlib import Path
import tempfile

from . import api, decisions
from .semantics import dumps, loads


CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "openrouter_models.json"
model_ids = ()
DECISIONS_CACHE_PATH = CACHE_PATH.with_name("openrouter_decisions.json")
TYPESAFE_MODELS = ("jev-latest", "jev-preview", "jev-1.13.0")
# Offline bootstrap for already-supported routes; live catalog metadata wins.
BUILTIN_DECISIONS = {
    model: {"input_modalities": ["text", "image"], "output_modalities": ["decisions"]}
    for model in decisions.MODELS
}
BUILTIN_DECISIONS.update({
    model: {"input_modalities": ["text"], "output_modalities": ["decisions"]}
    for model in ("~typesafe/jev-latest", "typesafe/jev-1.13")
})
decision_models = dict(BUILTIN_DECISIONS)
decision_model_ids = tuple(sorted(decision_models))


async def load():
    global model_ids
    try:
        model_ids = await api.list_text_models()
    except (RuntimeError, ValueError) as error:
        try:
            cached = loads(CACHE_PATH.read_text(encoding="utf-8"), "OpenRouter model cache")
            if isinstance(cached, list) and cached and all(isinstance(item, str) and item.strip() and item != "custom" for item in cached):
                model_ids = tuple(sorted(set(cached)))
        except (OSError, ValueError):
            pass
        fallback = "using the last model list" if model_ids else "use custom to enter a model ID"
        logging.warning("OpenRouter Text: %s; %s. Restart ComfyUI to retry.", error, fallback)
        return
    _save(CACHE_PATH, model_ids, "OpenRouter Text")


def _save(path, data, label):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(dumps(data))
        temporary.replace(path)
    except OSError:
        logging.warning("%s: could not save the model catalog; using the in-memory list.", label)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def load_decisions():
    global decision_models, decision_model_ids
    # Keep previously verified IDs in the schema so saved workflows survive
    # catalog removals. Only the latest successful list is offered for new choices.
    try:
        cached = loads(DECISIONS_CACHE_PATH.read_text(encoding="utf-8"), "OpenRouter Decisions cache")
        verified = api.decision_models_from_data(cached["data"])
        available = cached["available"]
        if not isinstance(available, list) or not available or not all(isinstance(item, str) and item in verified for item in available):
            raise ValueError("Invalid Decisions cache model list")
        decision_models = {**decision_models, **verified}
        decision_model_ids = tuple(sorted(set(available)))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        current = await api.list_decision_models()
    except (RuntimeError, ValueError) as error:
        logging.warning("OpenRouter Decisions: %s; using the last verified or bundled Decisions list. Restart ComfyUI to retry.", error)
        return
    decision_models = {**decision_models, **current}
    decision_model_ids = tuple(current)
    _save(DECISIONS_CACHE_PATH, {
        "data": [{"id": model, "architecture": architecture} for model, architecture in sorted(decision_models.items())],
        "available": decision_model_ids,
    }, "OpenRouter Decisions")

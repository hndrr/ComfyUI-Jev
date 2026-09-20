import logging
from pathlib import Path
import tempfile

from . import api
from .semantics import dumps, loads


CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "openrouter_models.json"
model_ids = ()


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
    temporary = None
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CACHE_PATH.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(dumps(model_ids))
        temporary.replace(CACHE_PATH)
    except OSError:
        logging.warning("OpenRouter Text: could not save the model catalog; using the in-memory list.")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

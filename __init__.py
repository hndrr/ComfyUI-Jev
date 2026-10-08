from .nodes import JevExtension
from . import model_catalog

WEB_DIRECTORY = "./web"


async def comfy_entrypoint():
    await model_catalog.load()
    await model_catalog.load_decisions()
    return JevExtension()

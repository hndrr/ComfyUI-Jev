from .nodes import JevExtension
from . import model_catalog


async def comfy_entrypoint():
    await model_catalog.load()
    return JevExtension()

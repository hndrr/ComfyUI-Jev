from .nodes import JevExtension


async def comfy_entrypoint():
    return JevExtension()
